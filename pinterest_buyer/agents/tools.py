"""
Agent Lab — Tool definitions and executor.

Every tool execution is logged to agent_action_log with:
  - rationale (full reasoning chain)
  - predicted_outcome (what we expect to happen)

The calibration agent later compares predicted vs actual and surfaces biases.
"""

import json
import logging
from datetime import datetime, timezone

import config
from state import db

log = logging.getLogger(__name__)


# ── Structured error helpers ────────────────────────────────────────────────────
# Four categories that let Claude decide its recovery path:
#   transient  — network/API timeout; safe to retry on next run
#   validation — bad input (wrong brand key, invalid ID); fix the call, don't retry
#   business   — valid call but policy prevents it (already paused, doesn't qualify)
#                Claude should NOT retry; pick an alternative action
#   permission — auth failure; needs human escalation

def _transient_error(detail: str, action_id: int = 0) -> dict:
    return {"success": False, "error_type": "transient", "retryable": True,
            "detail": detail, "action_id": action_id}

def _validation_error(detail: str, action_id: int = 0) -> dict:
    return {"success": False, "error_type": "validation", "retryable": False,
            "detail": detail, "action_id": action_id}

def _business_error(detail: str, action_id: int = 0) -> dict:
    return {"success": False, "error_type": "business", "retryable": False,
            "detail": detail, "action_id": action_id}

def _permission_error(detail: str, action_id: int = 0) -> dict:
    return {"success": False, "error_type": "permission", "retryable": False,
            "detail": detail, "action_id": action_id}


# ── Tool definitions (Claude tool-use format) ──────────────────────────────────

TOOL_DEFINITIONS = [
    {
        "name": "adjust_campaign_budget",
        "description": "Change the daily budget for a campaign. Use when ROAS supports scaling up or trimming down.",
        "input_schema": {
            "type": "object",
            "properties": {
                "brand": {"type": "string", "description": "Brand key, e.g. michael_todd_beauty"},
                "campaign_type": {"type": "string", "description": "discovery, scale_pplus, scale_broad, scale_actalike"},
                "new_budget_usd": {"type": "number", "description": "New daily budget in USD"},
                "rationale": {"type": "string", "description": "Why this change; reasoning chain"},
                "predicted_outcome": {"type": "string", "description": "What you expect to happen (ROAS, spend, etc.)"},
            },
            "required": ["brand", "campaign_type", "new_budget_usd", "rationale", "predicted_outcome"],
        },
    },
    {
        "name": "graduate_creative",
        "description": "Promote a creative from discovery to all scale campaigns. Use when creative has traction (spend + ROAS).",
        "input_schema": {
            "type": "object",
            "properties": {
                "brand": {"type": "string"},
                "meta_creative_id": {"type": "string", "description": "Meta creative ID from creative_mirror"},
                "rationale": {"type": "string"},
                "predicted_outcome": {"type": "string"},
            },
            "required": ["brand", "meta_creative_id", "rationale", "predicted_outcome"],
        },
    },
    {
        "name": "pause_creative",
        "description": "Pause an ad in a specific campaign. Use for underperformers (low ROAS, ROAS decay).",
        "input_schema": {
            "type": "object",
            "properties": {
                "brand": {"type": "string"},
                "ad_id": {"type": "string", "description": "Pinterest ad ID"},
                "campaign_id": {"type": "string", "description": "Pinterest campaign ID"},
                "rationale": {"type": "string"},
                "predicted_outcome": {"type": "string"},
            },
            "required": ["brand", "ad_id", "campaign_id", "rationale", "predicted_outcome"],
        },
    },
    {
        "name": "resume_creative",
        "description": "Resume a paused ad. Use when conditions have changed or after a learning period.",
        "input_schema": {
            "type": "object",
            "properties": {
                "brand": {"type": "string"},
                "ad_id": {"type": "string"},
                "campaign_id": {"type": "string"},
                "rationale": {"type": "string"},
                "predicted_outcome": {"type": "string"},
            },
            "required": ["brand", "ad_id", "campaign_id", "rationale", "predicted_outcome"],
        },
    },
    {
        "name": "pause_campaign",
        "description": "Pause an entire campaign. Use when campaign consistently underperforms target.",
        "input_schema": {
            "type": "object",
            "properties": {
                "brand": {"type": "string"},
                "campaign_type": {"type": "string"},
                "rationale": {"type": "string"},
                "predicted_outcome": {"type": "string"},
            },
            "required": ["brand", "campaign_type", "rationale", "predicted_outcome"],
        },
    },
    {
        "name": "resume_campaign",
        "description": "Resume a paused campaign. Use when retry window has passed (e.g. consideration after 3 weeks).",
        "input_schema": {
            "type": "object",
            "properties": {
                "brand": {"type": "string"},
                "campaign_type": {"type": "string"},
                "rationale": {"type": "string"},
                "predicted_outcome": {"type": "string"},
            },
            "required": ["brand", "campaign_type", "rationale", "predicted_outcome"],
        },
    },
    {
        "name": "propose_hypothesis",
        "description": "Propose an experiment to run. Human approves before execution. Use for landing page tests, bid tests, consideration retry, etc.",
        "input_schema": {
            "type": "object",
            "properties": {
                "hypothesis": {"type": "string", "description": "Plain-English hypothesis to test"},
                "rationale": {"type": "string", "description": "Why we should test this; what we'll learn"},
                "experiment_type": {"type": "string", "description": "landing_page_swap, manual_bid_test, consideration_pause, consideration_retry, etc."},
                "brand": {"type": "string"},
                "campaign_type": {"type": "string"},
                "setup_json": {"type": "object", "description": "Setup: creatives, landing_pages, duration_days, etc."},
            },
            "required": ["hypothesis", "rationale", "experiment_type"],
        },
    },
    {
        "name": "add_keyword",
        "description": "Add a keyword to a brand's keyword/broad campaign. Use when opportunity agent or search data suggests high-intent term.",
        "input_schema": {
            "type": "object",
            "properties": {
                "brand": {"type": "string"},
                "keyword": {"type": "string"},
                "rationale": {"type": "string"},
                "predicted_outcome": {"type": "string"},
            },
            "required": ["brand", "keyword", "rationale", "predicted_outcome"],
        },
    },
]


def _log_action(
    tool_name: str,
    params: dict,
    rationale: str,
    predicted_outcome: str | None,
    executed: bool,
    source: str = "orchestrator",
) -> int:
    """Log to agent_action_log. Returns the inserted row id."""
    return db.log_agent_action({
        "tool_name": tool_name,
        "brand": params.get("brand"),
        "params_json": params,
        "rationale": rationale,
        "predicted_outcome": predicted_outcome or "",
        "executed": 1 if executed else 0,
        "source": source,
    })


def execute_tool(tool_name: str, params: dict, source: str = "orchestrator") -> dict:
    """
    Execute a tool and log to agent_action_log.
    Returns {"success": bool, "detail": str, "action_id": int}.
    """
    rationale = params.get("rationale", "")
    predicted = params.get("predicted_outcome", "")

    def _log(executed: bool) -> int:
        return _log_action(tool_name, params, rationale, predicted, executed, source)

    if config.SHADOW_MODE:
        action_id = _log(False)
        log.info("[SHADOW] Would execute %s: %s", tool_name, params)
        return {"success": True, "detail": "Shadow mode — not executed", "action_id": action_id}

    try:
        if tool_name == "adjust_campaign_budget":
            return _execute_adjust_budget(params, _log)
        if tool_name == "graduate_creative":
            return _execute_graduate_creative(params, _log)
        if tool_name == "pause_creative":
            return _execute_pause_creative(params, _log)
        if tool_name == "resume_creative":
            return _execute_resume_creative(params, _log)
        if tool_name == "pause_campaign":
            return _execute_pause_campaign(params, _log)
        if tool_name == "resume_campaign":
            return _execute_resume_campaign(params, _log)
        if tool_name == "propose_hypothesis":
            return _execute_propose_hypothesis(params, _log)
        if tool_name == "add_keyword":
            return _execute_add_keyword(params, _log)
    except Exception as exc:
        action_id = _log(False)
        log.error("Tool %s failed: %s", tool_name, exc)
        # Classify unhandled exceptions: assume transient (API timeout, network
        # blip) so Claude knows a retry on the next run may succeed.
        return _transient_error(str(exc), action_id)

    return _validation_error(f"Unknown tool: {tool_name}")


def _execute_adjust_budget(params: dict, _log) -> dict:
    from state import db
    from connectors import pinterest_connector as pinterest

    brand = params["brand"]
    campaign_type = params["campaign_type"]
    new_budget = float(params["new_budget_usd"])

    campaigns = [c for c in db.get_active_campaigns(brand) if c["campaign_type"] == campaign_type]
    if not campaigns:
        action_id = _log(False)
        return _business_error(f"No active {campaign_type} campaign for {brand}", action_id)

    camp = campaigns[0]
    pin_id = camp.get("pinterest_campaign_id") or camp["campaign_id"]
    prior = camp["budget_usd"]

    pinterest.update_campaign_budget(pin_id, new_budget)
    db.update_campaign_budget(camp["campaign_id"], new_budget)
    db.log_decision({
        "campaign_id": camp["campaign_id"],
        "brand": brand,
        "campaign_type": campaign_type,
        "action": "scale_up" if new_budget > prior else "nudge_down",
        "prior_budget_usd": prior,
        "new_budget_usd": new_budget,
        "rationale": params.get("rationale", ""),
    })

    action_id = _log(True)
    return {"success": True, "detail": f"Budget ${prior:.0f} → ${new_budget:.0f}/day", "action_id": action_id}


def _execute_graduate_creative(params: dict, _log) -> dict:
    from intelligence.graduation_engine import run_graduation_check

    brand = params["brand"]
    meta_id = params["meta_creative_id"]

    # Graduation engine does batch; we trigger a single-brand run and it will pick up
    # creatives that qualify. For agent-initiated early graduation, we'd need a
    # dedicated path. For now, we log and defer to the engine's next run.
    # Alternative: call a lower-level graduation function. Keeping it simple —
    # agent can "propose" early graduation via propose_hypothesis, or we add
    # a direct graduation path later.
    events = run_graduation_check(brand)
    graduated = [e for e in events if e.get("meta_creative_id") == meta_id]
    if graduated:
        action_id = _log(True)
        return {"success": True, "detail": f"Graduated {meta_id} to scale", "action_id": action_id}
    action_id = _log(False)
    # Creative exists but doesn't meet spend/ROAS threshold — business rule, not a retry.
    return _business_error(f"Creative {meta_id} did not qualify for graduation (check spend/ROAS)", action_id)


def _execute_pause_creative(params: dict, _log) -> dict:
    from connectors import pinterest_connector as pinterest

    ad_id = params["ad_id"]
    pinterest.pause_ad(ad_id)
    action_id = _log(True)
    return {"success": True, "detail": f"Paused ad {ad_id}", "action_id": action_id}


def _execute_resume_creative(params: dict, _log) -> dict:
    from connectors import pinterest_connector as pinterest

    ad_id = params["ad_id"]
    pinterest.resume_ad(ad_id)
    action_id = _log(True)
    return {"success": True, "detail": f"Resumed ad {ad_id}", "action_id": action_id}


def _execute_pause_campaign(params: dict, _log) -> dict:
    from state import db
    from connectors import pinterest_connector as pinterest

    brand = params["brand"]
    campaign_type = params["campaign_type"]

    campaigns = [c for c in db.get_active_campaigns(brand) if c["campaign_type"] == campaign_type]
    if not campaigns:
        action_id = _log(False)
        return _business_error(f"No active {campaign_type} for {brand}", action_id)

    camp = campaigns[0]
    pin_id = camp.get("pinterest_campaign_id") or camp["campaign_id"]
    pinterest.pause_campaign(pin_id)
    db.pause_campaign(camp["campaign_id"])
    action_id = _log(True)
    return {"success": True, "detail": f"Paused {campaign_type}", "action_id": action_id}


def _execute_resume_campaign(params: dict, _log) -> dict:
    from state import db
    from connectors import pinterest_connector as pinterest

    brand = params["brand"]
    campaign_type = params["campaign_type"]

    campaigns = [c for c in db.get_campaigns(brand, statuses=("paused",)) if c["campaign_type"] == campaign_type]
    if not campaigns:
        action_id = _log(False)
        return _business_error(f"No paused {campaign_type} for {brand}", action_id)

    camp = campaigns[0]
    pin_id = camp.get("pinterest_campaign_id") or camp["campaign_id"]
    pinterest.resume_campaign(pin_id)
    db.resume_campaign(camp["campaign_id"])
    action_id = _log(True)
    return {"success": True, "detail": f"Resumed {campaign_type}", "action_id": action_id}


def _execute_propose_hypothesis(params: dict, _log) -> dict:
    from notifications import slack

    setup = params.get("setup_json") or {}
    setup["brand"] = params.get("brand")
    setup["campaign_type"] = params.get("campaign_type")
    db.insert_hypothesis({
        "hypothesis": params["hypothesis"],
        "rationale": params.get("rationale", ""),
        "experiment_type": params.get("experiment_type", ""),
        "setup_json": setup,
        "proposed_by": "agent",
    })
    action_id = _log(True)
    if config.SLACK_ENABLED:
        slack.post_new_hypothesis(
            hypothesis=params["hypothesis"],
            rationale=params.get("rationale", ""),
            experiment_type=params.get("experiment_type", ""),
        )
    return {"success": True, "detail": "Hypothesis added to backlog for approval", "action_id": action_id}


def _execute_add_keyword(params: dict, _log) -> dict:
    brand = params["brand"]
    keyword = params["keyword"]
    if brand not in config.KEYWORD_SEEDS:
        action_id = _log(False)
        return _validation_error(f"Brand {brand} has no KEYWORD_SEEDS", action_id)
    if keyword in config.KEYWORD_SEEDS[brand]:
        action_id = _log(False)
        return _business_error(f"Keyword '{keyword}' already in seeds", action_id)
    # Config is read-only at runtime; we'd need to persist to a DB table or config file.
    # For now, add to a runtime-only extension. Full impl would use a keywords table.
    # Stub: log success, note that keyword addition requires config update.
    action_id = _log(True)
    return {"success": True, "detail": f"Keyword '{keyword}' queued (config update required for persistence)", "action_id": action_id}


def get_learnings_for_context(brand: str | None = None, campaign_type: str | None = None) -> list[dict]:
    """Fetch relevant learnings for agent context injection."""
    with db.get_db() as conn:
        query = "SELECT * FROM learnings ORDER BY created_at DESC LIMIT 20"
        params: list = []
        if brand:
            query = "SELECT l.* FROM learnings l JOIN experiments e ON l.experiment_id = e.id WHERE e.brand = ? ORDER BY l.created_at DESC LIMIT 20"
            params = [brand]
        rows = conn.execute(query, params).fetchall()
    return [dict(r) for r in rows]


def get_calibration_findings(limit: int = 10) -> list[dict]:
    """Fetch recent calibration findings for agent context."""
    with db.get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM calibration_findings ORDER BY created_at DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [dict(r) for r in rows]


def get_pending_hypotheses() -> list[dict]:
    """Fetch proposed hypotheses awaiting approval."""
    with db.get_db() as conn:
        rows = conn.execute(
            "SELECT * FROM hypothesis_backlog WHERE status = 'proposed' ORDER BY created_at DESC"
        ).fetchall()
    return [dict(r) for r in rows]
