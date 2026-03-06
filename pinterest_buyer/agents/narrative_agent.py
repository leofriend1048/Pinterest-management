"""
Narrative agent — writes the morning digest.

Every number is compared to a relevant baseline.
Output is plain English, not markdown soup.

Morning digest structure
1. Yesterday's scorecard — spend, ROAS, NCPA per brand vs. 7-day avg
2. Creatives that graduated to scale overnight
3. Budget decisions made and why
4. Ads paused and why
5. Anomalies (P0/P1 from anomaly agent)
6. One top observation or opportunity
"""

import json
import logging
from datetime import date

import anthropic

import config
from state import db

log = logging.getLogger(__name__)
_client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)


_SYSTEM_PROMPT = """You are a senior DTC performance buyer writing the morning Pinterest account
digest for Leo, CMO at Michael Todd Beauty. Leo is data-savvy but wants plain English,
not jargon. Every number must be compared to a baseline (7-day avg, 30-day avg, or target).

Digest structure (follow this exactly):
1. **Yesterday's Scorecard** — spend, ROAS, NCPA per brand vs. 7-day average
2. **Creatives Graduated to Scale** — names, spend at graduation, ROAS, which 4 lanes entered
3. **Budget Decisions** — what changed, why, ROAS basis
4. **Ads Paused** — what got paused, why, spend to date
5. **Anomalies** — P0/P1 items only; P2 omitted from digest
6. **Top Observation** — one insight or opportunity worth acting on

Tone: confident, brief, no filler. Write as if you manage the account yourself.
Use specific numbers. No hedging. No "it appears" or "it seems."
Target length: 400–600 words."""


def write_morning_digest(
    anomaly_result: dict,
    graduation_events: list[dict],
    budget_decisions: list[dict],
    paused_ads: list[dict],
) -> str:
    """
    Generate and return the morning digest text.
    """
    yesterday  = db.get_yesterday_summary()
    baseline_7 = db.get_baseline_summary(days=7)

    # Build context payload
    context = {
        "date":                date.today().isoformat(),
        "yesterday_performance": yesterday,
        "seven_day_baseline":    baseline_7,
        "target_roas":           config.TARGET_ROAS,
        "graduations_overnight": graduation_events,
        "budget_decisions":      [
            {k: v for k, v in d.items() if k not in ("shadow_mode",)}
            for d in budget_decisions
        ],
        "ads_paused":           paused_ads,
        "anomalies":            [
            a for a in anomaly_result.get("anomalies", [])
            if a.get("priority") in ("P0", "P1")
        ],
        "anomaly_summary":      anomaly_result.get("summary", ""),
    }

    user_message = (
        "Write today's morning digest based on the data below.\n\n"
        f"```json\n{json.dumps(context, indent=2, default=str)}\n```"
    )

    log.info("Calling narrative agent...")
    response = _client.messages.create(
        model=config.CLAUDE_MODEL,
        max_tokens=2048,
        system=_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_message}],
    )

    digest = response.content[0].text.strip()
    log.info("Narrative agent complete (%d chars).", len(digest))
    return digest
