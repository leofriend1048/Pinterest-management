"""
Anomaly detection agent.

Receives a structured snapshot of account performance across four timeframes
(daily, weekly, monthly, quarterly), sends it to Claude, and returns a
structured list of anomalies classified as P0, P1, or P2.

P0/P1 → bundled into morning digest
P2    → monitor (logged, not alerted)
"""

import json
import logging
from datetime import date, timedelta

import anthropic

import config
from connectors import northbeam_connector
from state import db

log = logging.getLogger(__name__)
_client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)


_SYSTEM_PROMPT = """You are a senior DTC performance buyer analyzing Pinterest ad account data
for Michael Todd Beauty (SonicSmooth).

You will receive a structured JSON snapshot of account performance across four timeframes
(daily, weekly, monthly, quarterly) for 4 campaign types: discovery, actalike scale,
performance+ scale, and broad/interest scale.

Identify ALL anomalies. For each anomaly, return structured JSON with these fields:
  - priority: "P0" | "P1" | "P2"
  - brand: brand name
  - campaign_type: campaign type or "account_wide"
  - timeframe: "daily" | "weekly" | "monthly" | "quarterly"
  - what_changed: concise description of the change
  - magnitude: quantified magnitude (e.g. "-42% ROAS vs 7-day avg")
  - root_cause: most likely explanation
  - recommended_action: specific, actionable recommendation
  - narrative: 2–3 sentence plain-English summary for the morning digest

Priority definitions:
  P0 — Requires immediate attention. Examples: spend spike/drop >30%, ROAS collapse,
       delivery failure, CPM anomaly that will blow the budget.
  P1 — Needs attention within 24h. Examples: efficiency trend reversal, creative fatigue
       inflection, budget pacing off-track, scale campaign divergence.
       ALWAYS classify as P1 when new_visitor percentage falls below the floor for the
       campaign type for 2+ consecutive days:
         discovery      → floor 70%  (healthy 80–95%)
         scale_actalike → floor 40%  (healthy 50–75%)
         scale_pplus    → floor 50%  (healthy 60–80%)
         scale_broad    → floor 60%  (healthy 70–85%)
       New visitor rate below floor = prospecting pool is shrinking = ecosystem health risk.
       Tag these anomalies as campaign_type = the specific type, timeframe = "daily".
  P2 — Monitor. Examples: early signals worth watching, keyword drift, audience saturation
       starting to show. New visitor rate trending toward floor but not yet breached = P2.

Return ONLY valid JSON with this structure:
{
  "anomalies": [...],
  "summary": "1-2 sentence overall account health summary"
}"""


def _build_snapshot() -> dict:
    """Build the multi-timeframe performance snapshot JSON."""
    today = date.today().isoformat()

    def _window(days: int) -> list[dict]:
        return db.get_baseline_summary(days=days)

    yesterday  = db.get_yesterday_summary()
    w7_base    = _window(7)
    w30_base   = _window(30)
    w90_base   = _window(90)

    # Fetch new_visitors from Northbeam for 1d and 7d windows
    try:
        nb_1d = northbeam_connector.fetch_multiwindow_campaign_roas(windows=(1,)).get(1, {})
        nb_7d = northbeam_connector.fetch_multiwindow_campaign_roas(windows=(7,)).get(7, {})
        new_visitors_1d = {cid: d.get("new_visitors", 0) for cid, d in nb_1d.items()}
        new_visitors_7d = {cid: d.get("new_visitors", 0) for cid, d in nb_7d.items()}
    except Exception as exc:
        log.warning("Could not fetch new_visitors from Northbeam: %s", exc)
        new_visitors_1d = {}
        new_visitors_7d = {}

    # Recent budget decisions
    with db.get_db() as conn:
        recent_decisions = [
            dict(r) for r in conn.execute(
                "SELECT * FROM decision_audit ORDER BY timestamp DESC LIMIT 50"
            ).fetchall()
        ]
        recent_graduations = [
            dict(r) for r in conn.execute(
                "SELECT * FROM graduation_log ORDER BY graduation_date DESC LIMIT 20"
            ).fetchall()
        ]

    return {
        "snapshot_date": today,
        "brands":        config.BRANDS,
        "campaign_types": config.CAMPAIGN_TYPES,
        "target_roas":   config.TARGET_ROAS,
        "daily": {
            "performance": yesterday,
            "description": "Yesterday's actuals per brand/campaign_type",
        },
        "weekly": {
            "baseline_7d": w7_base,
            "description": "7-day rolling average per brand/campaign_type",
        },
        "monthly": {
            "baseline_30d": w30_base,
            "description": "30-day rolling average per brand/campaign_type",
        },
        "quarterly": {
            "baseline_90d": w90_base,
            "description": "90-day rolling average per brand/campaign_type",
        },
        "recent_budget_decisions": recent_decisions,
        "recent_graduations":      recent_graduations,
        "new_visitors": {
            "yesterday_by_campaign": new_visitors_1d,
            "7d_by_campaign":        new_visitors_7d,
            "description": "Count of new (first-time) visitors attributed to each campaign by Northbeam",
        },
    }


def run_anomaly_detection() -> dict:
    """
    Run the anomaly detection agent and return the parsed result.

    Returns:
    {
        "anomalies": [
            {
                "priority": "P0"|"P1"|"P2",
                "brand": str,
                "campaign_type": str,
                "timeframe": str,
                "what_changed": str,
                "magnitude": str,
                "root_cause": str,
                "recommended_action": str,
                "narrative": str,
            }
        ],
        "summary": str,
    }
    """
    snapshot = _build_snapshot()

    user_message = (
        "Below is the structured account performance snapshot. "
        "Identify all anomalies, classify them, and return the result as valid JSON.\n\n"
        f"```json\n{json.dumps(snapshot, indent=2, default=str)}\n```"
    )

    log.info("Calling anomaly detection agent...")
    response = _client.messages.create(
        model=config.CLAUDE_MODEL,
        max_tokens=4096,
        system=_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_message}],
    )

    raw_text = response.content[0].text.strip()

    # Strip markdown code fences if present
    if raw_text.startswith("```"):
        raw_text = raw_text.split("```")[1]
        if raw_text.startswith("json"):
            raw_text = raw_text[4:]
        raw_text = raw_text.strip()

    try:
        result = json.loads(raw_text)
    except json.JSONDecodeError as exc:
        log.error("Anomaly agent returned invalid JSON: %s", exc)
        result = {"anomalies": [], "summary": "Anomaly detection failed — invalid JSON response."}

    p_counts = {"P0": 0, "P1": 0, "P2": 0}
    for a in result.get("anomalies", []):
        p_counts[a.get("priority", "P2")] = p_counts.get(a.get("priority", "P2"), 0) + 1

    log.info(
        "Anomaly detection complete: P0=%d, P1=%d, P2=%d",
        p_counts["P0"], p_counts["P1"], p_counts["P2"],
    )
    return result
