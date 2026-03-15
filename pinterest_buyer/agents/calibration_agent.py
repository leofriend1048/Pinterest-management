"""
Calibration agent — predicted vs actual review.

Periodically reviews agent_action_log: compares predicted outcomes to actual
outcomes (from performance_snapshots) and surfaces calibration errors.

Example: "Agent consistently over-estimates ROAS lift from budget increases on
cold audiences" — this feedback tightens the orchestrator's judgment over time
without retraining.
"""

import json
import logging
from datetime import date, timedelta

import anthropic

import config
from state import db
from notifications import slack

log = logging.getLogger(__name__)
_client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)


_SYSTEM_PROMPT = """You are a calibration analyst for a Pinterest media buying AI agent.

You receive a list of agent actions with:
  - rationale: why the agent took the action
  - predicted_outcome: what the agent expected to happen
  - actual_outcome: what actually happened (from performance data)

Your job: identify systematic calibration errors — patterns where the agent
consistently over- or under-estimates outcomes.

Examples of calibration errors:
  - "Agent over-estimates ROAS lift from budget scale-ups on discovery campaigns"
  - "Agent under-estimates how quickly creatives fatigue after graduation"
  - "Agent over-estimates benefit of pausing underperformers (ROAS didn't recover)"
  - "Budget increases on cold audiences consistently deliver below predicted ROAS"

Return ONLY valid JSON:
{
  "findings": [
    {
      "finding_type": "overestimate" | "underestimate" | "wrong_timing" | "no_pattern",
      "pattern": "Brief description of the pattern",
      "evidence": "What the data shows (e.g. 5/7 budget scale-ups underperformed)",
      "recommended_correction": "Specific guidance for the agent (e.g. Apply 0.8× discount to predicted ROAS lift for discovery)"
    }
  ],
  "summary": "1-2 sentence overall calibration health"
}

If no systematic errors are found, return findings: [] and summary explaining that.
Only include findings where there is clear evidence (3+ actions showing the pattern).
"""


def _compute_actual_outcome(action: dict) -> str:
    """
    Compute actual outcome for an action based on performance data.
    Returns a plain-English summary.
    """
    params = {}
    try:
        params = json.loads(action.get("params_json") or "{}")
    except Exception:
        pass

    tool_name = action.get("tool_name", "")
    brand = action.get("brand") or params.get("brand")
    if not brand:
        return "Unknown brand — cannot compute outcome"

    # Look at performance 7–14 days after the action
    ts = action.get("timestamp", "")
    try:
        action_date = date.fromisoformat(ts[:10])
    except Exception:
        return "Invalid timestamp"
    window_start = (action_date + timedelta(days=7)).isoformat()
    window_end = (action_date + timedelta(days=14)).isoformat()

    if tool_name == "adjust_campaign_budget":
        campaign_type = params.get("campaign_type")
        # Use last 7 days of performance as outcome proxy (action is 7+ days old)
        perf = db.get_performance_window(brand, 7, campaign_type)
        if not perf:
            return "No performance data in last 7 days"
        avg_roas = sum(p.get("roas") or 0 for p in perf) / len(perf)
        total_spend = sum(p.get("spend_usd") or 0 for p in perf)
        target = config.TARGET_ROAS.get(brand, 2.5)
        vs_target = (avg_roas / target * 100) if target else 0
        return f"L7 post-action: avg ROAS {avg_roas:.2f}× ({vs_target:.0f}% of target), spend ${total_spend:.0f}"

    if tool_name == "pause_creative":
        return "Creative paused — no post-pause performance to measure"

    if tool_name == "graduate_creative":
        meta_id = params.get("meta_creative_id")
        # Check graduation_log and performance for this creative in scale
        perf = db.get_performance_window(brand, 14)
        # Simplified: report that we'd need ad-level Northbeam data for precise measurement
        return "Graduation executed — scale campaign ROAS would need ad-level attribution to measure creative impact"

    if tool_name == "pause_campaign" or tool_name == "resume_campaign":
        return "Campaign status changed — measure by comparing pre/post period ROAS"

    if tool_name == "propose_hypothesis":
        return "Hypothesis proposed — outcome measured when experiment completes"

    return "Outcome not auto-computable for this tool type"


def run_calibration_review() -> dict:
    """
    Run the calibration agent: fetch unmeasured actions, compute actuals,
    compare to predicted, surface calibration findings.

    Returns:
    {
        "actions_reviewed": int,
        "findings": [...],
        "summary": str,
    }
    """
    min_age = getattr(config, "CALIBRATION_LOOKBACK_DAYS", 14)
    min_actions = getattr(config, "CALIBRATION_MIN_ACTIONS", 5)

    actions = db.get_unmeasured_actions(min_age_days=min_age)
    if len(actions) < min_actions:
        log.info("Calibration: only %d unmeasured actions (need %d) — skipping", len(actions), min_actions)
        return {"actions_reviewed": 0, "findings": [], "summary": "Insufficient data for calibration review."}

    # Compute actual outcome for each
    for a in actions:
        actual = _compute_actual_outcome(a)
        a["actual_outcome"] = actual
        db.update_action_outcome(a["id"], actual)

    # Build comparison payload for Claude
    comparisons = []
    for a in actions:
        params = {}
        try:
            params = json.loads(a.get("params_json") or "{}")
        except Exception:
            pass
        comparisons.append({
            "tool_name": a["tool_name"],
            "brand": a.get("brand"),
            "rationale": a.get("rationale", ""),
            "predicted_outcome": a.get("predicted_outcome", ""),
            "actual_outcome": a.get("actual_outcome", ""),
            "params_summary": {k: v for k, v in params.items() if k not in ("rationale", "predicted_outcome")},
        })

    user_message = (
        "Below are agent actions with predicted vs actual outcomes. "
        "Identify any systematic calibration errors.\n\n"
        f"```json\n{json.dumps(comparisons, indent=2, default=str)}\n```"
    )

    log.info("Running calibration review on %d actions...", len(actions))
    response = _client.messages.create(
        model=config.CLAUDE_MODEL,
        max_tokens=2048,
        system=_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_message}],
    )

    raw_text = response.content[0].text.strip()
    if raw_text.startswith("```"):
        raw_text = raw_text.split("```")[1]
        if raw_text.startswith("json"):
            raw_text = raw_text[4:]
        raw_text = raw_text.strip()

    try:
        result = json.loads(raw_text)
    except json.JSONDecodeError as exc:
        log.error("Calibration agent returned invalid JSON: %s", exc)
        result = {"findings": [], "summary": "Calibration analysis failed — invalid JSON."}

    findings = result.get("findings", [])
    summary = result.get("summary", "")

    # Store calibration findings
    action_ids = [a["id"] for a in actions]
    for f in findings:
        if f.get("finding_type") != "no_pattern":
            db.insert_calibration_finding({
                "finding_type": f.get("finding_type", "overestimate"),
                "pattern": f.get("pattern", ""),
                "evidence": f.get("evidence", ""),
                "recommended_correction": f.get("recommended_correction", ""),
                "action_ids": action_ids,
            })

    # Slack summary if there are findings
    if findings and config.SLACK_ENABLED:
        slack.post_calibration_summary(
            actions_reviewed=len(actions),
            findings=findings,
            summary=summary,
        )

    log.info("Calibration complete: %d actions reviewed, %d findings", len(actions), len(findings))
    return {
        "actions_reviewed": len(actions),
        "findings": findings,
        "summary": summary,
    }
