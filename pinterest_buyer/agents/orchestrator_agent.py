"""
Orchestrator agent — tool-equipped media buying operator.

Receives full account snapshot (campaigns, creatives, performance, learnings,
calibration findings), reasons with Claude, and executes tools to:
  - Adjust budgets, graduate creatives, pause/resume ads and campaigns
  - Propose hypotheses for human approval
  - Add keywords

Every action is logged with rationale + predicted_outcome for the calibration agent.
"""

import json
import logging
from datetime import date, timedelta

import anthropic

import config
from state import db
from agents import tools as agent_tools
from skills.loader import build_skill_context, list_skill_metadata, select_skills_for_snapshot

log = logging.getLogger(__name__)
_client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)


_SYSTEM_PROMPT = """You are the PAMBS orchestrator — an AI media buying operator for Pinterest ads for Michael Todd Beauty (SonicSmooth).

IMPORTANT: A rule-based budget engine and ad-pause auditor have ALREADY run before you. Their decisions are listed in the snapshot under `already_executed_today`. Do NOT duplicate their work:
  - Do NOT re-budget a campaign that already received a budget change today (the one-change gate will block it anyway, but don't waste a tool call)
  - Do NOT re-pause an ad the auditor already paused today

Your role is STRATEGIC — act on things the rules cannot handle:
  1. Propose experiments (landing page tests, bid tests, retry after pause window) — these require human approval
  2. Resume campaigns when retry windows have passed (e.g. paused 3+ weeks → propose resume)
  3. Diagnose root causes behind anomalies and propose hypotheses instead of blunt budget cuts
  4. Graduate creatives with unusual traction the rule-based engine may not have caught yet
  5. Flag ecosystem health signals (new visitor rate, frequency, CPM trends) as hypotheses

CRITICAL: Every tool call MUST include:
  - rationale: Your full reasoning chain — why this action, what pattern or data drove it
  - predicted_outcome: Explicit prediction (e.g. "ROAS will hold at 2.8× or improve", "Creative will scale to 2.5× in Actalike")

These are logged for calibration. A second agent compares your predictions to actual outcomes and surfaces biases. Be specific and honest.

Constraints:
  - Max {max_actions} actions per run. Prioritize highest-impact.
  - Do NOT propose hypotheses we've already tested (check learnings).
  - Apply calibration corrections when provided (e.g. "apply 0.8× to discovery ROAS predictions").
  - Consideration campaign: learnings suggest retry after 2–4 week pause. If paused 3+ weeks, propose resume.

If no actions are warranted, return no tool calls. It's better to hold than to act on noise.

{skill_metadata}"""


def _build_snapshot(context: dict | None = None) -> dict:
    """Build the full snapshot for the orchestrator."""
    yesterday = db.get_yesterday_summary()
    w7 = db.get_baseline_summary(7)
    w14 = db.get_baseline_summary(14)

    campaigns = db.get_active_campaigns()
    campaigns += [dict(c) for c in db.get_campaigns(statuses=("paused",))]

    learnings = agent_tools.get_learnings_for_context(brand="michael_todd_beauty")
    calibration = agent_tools.get_calibration_findings(limit=8)
    hypotheses = agent_tools.get_pending_hypotheses()

    with db.get_db() as conn:
        recent_actions = [
            dict(r) for r in conn.execute(
                "SELECT tool_name, brand, rationale, predicted_outcome, executed FROM agent_action_log ORDER BY timestamp DESC LIMIT 30"
            ).fetchall()
        ]

    # already_executed_today: what the rule-based engines did this run
    # Orchestrator must NOT duplicate these decisions.
    already_executed_today = {
        "budget_decisions": (context or {}).get("budget_decisions", []),
        "paused_ads":       (context or {}).get("paused_ads", []),
        "graduation_events": (context or {}).get("graduation_events", []),
    }

    return {
        "snapshot_date": date.today().isoformat(),
        "brands": config.BRANDS,
        "target_roas": config.TARGET_ROAS,
        "campaigns": [{"brand": c["brand"], "campaign_type": c["campaign_type"], "status": c["status"], "budget_usd": c["budget_usd"]} for c in campaigns],
        "yesterday": yesterday,
        "baseline_7d": w7,
        "baseline_14d": w14,
        "learnings": learnings[:10],
        "calibration_findings": calibration[:8],
        "pending_hypotheses": hypotheses[:5],
        "recent_agent_actions": recent_actions[:15],
        "already_executed_today": already_executed_today,
    }


def _tools_for_api() -> list[dict]:
    """Convert TOOL_DEFINITIONS to Anthropic API format."""
    return [
        {
            "name": t["name"],
            "description": t["description"],
            "input_schema": t["input_schema"],
        }
        for t in agent_tools.TOOL_DEFINITIONS
    ]


def run_orchestrator(context: dict | None = None) -> list[dict]:
    """
    Run the orchestrator agent. Returns list of executed actions.

    Args:
        context: Dict with keys 'budget_decisions', 'paused_ads', 'graduation_events'
                 — what the rule-based engines already did this run. Used to populate
                 already_executed_today so the orchestrator doesn't duplicate decisions.
    """
    if not getattr(config, "AGENT_LAB_ENABLED", True):
        log.info("Agent Lab disabled — skipping orchestrator.")
        return []

    snapshot = _build_snapshot(context)
    max_actions = getattr(config, "ORCHESTRATOR_MAX_ACTIONS", 10)

    selected_skills = select_skills_for_snapshot(snapshot)
    playbook_context = build_skill_context(selected_skills)
    log.info("Orchestrator loading skills: %s", selected_skills)

    # System prompt as a list of blocks. The playbook block carries cache_control
    # so it is cached by Anthropic for the duration of the tool-use loop —
    # preventing it from being re-billed on every round (up to 10 rounds/run).
    system: list[dict] = [
        {
            "type": "text",
            "text": _SYSTEM_PROMPT.format(
                max_actions=max_actions,
                skill_metadata=list_skill_metadata(),
            ),
        },
    ]
    if playbook_context:
        system.append({
            "type": "text",
            "text": playbook_context,
            "cache_control": {"type": "ephemeral"},
        })

    user_content = (
        "Review the account snapshot below. Decide which actions to take. "
        "Use your tools — each call must include rationale and predicted_outcome.\n\n"
        f"```json\n{json.dumps(snapshot, indent=2, default=str)}\n```"
    )

    messages = [{"role": "user", "content": user_content}]
    actions_executed: list[dict] = []

    # Primary stop: stop_reason == "end_turn" (Claude signals it's done).
    # Safety backstop: MAX_ROUNDS prevents runaway loops in case of bugs.
    MAX_ROUNDS = 10
    round_num = 0

    while round_num < MAX_ROUNDS:
        round_num += 1
        log.info("Orchestrator round %d — calling Claude...", round_num)
        response = _client.messages.create(
            model=config.CLAUDE_MODEL,
            max_tokens=4096,
            system=system,
            messages=messages,
            tools=_tools_for_api(),
        )

        # Claude signals it has finished reasoning and acting.
        if response.stop_reason == "end_turn":
            break

        # Collect tool uses from this turn (stop_reason == "tool_use")
        tool_uses = [b for b in response.content if hasattr(b, "type") and b.type == "tool_use"]
        if not tool_uses:
            break

        # Execute each tool and build tool results
        tool_results = []
        for block in tool_uses:
            tool_name = block.name
            try:
                params = json.loads(block.input) if isinstance(block.input, str) else block.input
            except (json.JSONDecodeError, TypeError):
                params = {}
            params.setdefault("rationale", "")
            params.setdefault("predicted_outcome", "")

            result = agent_tools.execute_tool(tool_name, params)
            actions_executed.append({
                "tool": tool_name,
                "params": params,
                "result": result,
            })
            tool_results.append({
                "type": "tool_result",
                "tool_use_id": block.id,
                "content": json.dumps({"success": result["success"], "detail": result["detail"]}),
            })

        # Serialize content blocks to plain dicts before appending — the SDK's
        # Pydantic models fail to re-serialize when passed back in messages.
        def _serialize_block(b) -> dict:
            if b.type == "text":
                return {"type": "text", "text": b.text}
            if b.type == "tool_use":
                return {"type": "tool_use", "id": b.id, "name": b.name, "input": b.input}
            return {"type": b.type}

        messages.append({
            "role": "assistant",
            "content": [_serialize_block(b) for b in response.content],
        })
        messages.append({"role": "user", "content": tool_results})

        if len(actions_executed) >= max_actions:
            log.info("Orchestrator hit max actions limit (%d).", max_actions)
            break

    log.info("Orchestrator complete — %d actions executed.", len(actions_executed))
    return actions_executed
