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

Data sources in the context:
- northbeam_account / northbeam_campaigns / northbeam_ads: Northbeam-attributed
  spend, revenue, ROAS, conversions, new_visitors for 1d / 3d / 7d windows.
  Use these as the primary ROAS and revenue source — they reflect true attribution.
- pinterest_platform: Pinterest-reported spend, impressions, clicks, CTR, CPM.
  Use these for delivery and efficiency metrics (CTR, CPM, impressions).
- ad_sets: Pinterest platform metrics at ad group (ad set) level for yesterday and 7d.

Digest structure (follow this exactly):
1. **Yesterday's Scorecard** — Northbeam 1d spend, ROAS, revenue, conversions vs. 3d and
   7d averages. Then Pinterest platform: total impressions, CTR, CPM vs. 7d baseline.
   Flag any significant new_visitors trends (rising / falling prospecting share).
2. **Campaign Breakdown** — For each active campaign: Northbeam 1d ROAS vs. 7d ROAS,
   spend, and direction vs. target. Keep each entry to one line.
3. **Ad Set Breakdown** — Top ad sets by yesterday spend: spend, ROAS (Northbeam if
   available, else Pinterest), CTR. Flag any outliers vs. campaign average.
4. **Top Ads** — Top 5 ads by Northbeam 1d spend with 1d ROAS vs. 7d ROAS. Flag any
   with 1d ROAS < 50% of 7d ROAS (decay signal).
5. **Creatives Graduated to Scale** — names, spend at graduation, ROAS, which lanes entered.
   Omit section if list is empty.
6. **Budget Decisions** — what changed, why, ROAS basis. Omit if none.
7. **Ads Paused** — what got paused, why, spend to date. Omit if none.
8. **Anomalies** — P0/P1 items only; P2 omitted from digest.
9. **Agent Actions** — include ONLY when orchestrator_actions is non-empty; for each:
   tool name, campaign, one-line rationale, predicted outcome.
10. **Weekly Opportunities** — include ONLY on Mondays when opportunity_report is present.
11. **Top Observation** — one insight or action item. Surface calibration bias corrections
    here if calibration_summary is present.

Tone: confident, brief, no filler. Write as if you manage the account yourself.
Use specific numbers. No hedging. No "it appears" or "it seems."
If a data section is empty or has no history yet (days_of_data=0), skip it cleanly —
do not fabricate numbers or say "data unavailable" more than once.
Target length: 500–700 words (add ~150 words on Mondays when opportunity report is included)."""


def write_morning_digest(
    anomaly_result: dict,
    graduation_events: list[dict],
    budget_decisions: list[dict],
    paused_ads: list[dict],
    opportunity_report: str | None = None,
    orchestrator_actions: list[dict] | None = None,
    calibration_summary: str | None = None,
) -> str:
    """
    Generate and return the morning digest text.
    """
    # Pinterest platform (from performance_snapshots)
    yesterday_platform  = db.get_yesterday_summary()
    baseline_7_platform = db.get_baseline_summary(days=7)

    # Northbeam attributed data across all windows (from northbeam_daily_stats)
    nb_account = {
        "1d": db.get_northbeam_account_window(1),
        "3d": db.get_northbeam_account_window(3),
        "7d": db.get_northbeam_account_window(7),
    }
    nb_campaigns = {
        "1d": db.get_northbeam_campaign_window(1),
        "3d": db.get_northbeam_campaign_window(3),
        "7d": db.get_northbeam_campaign_window(7),
    }
    nb_ads_1d = db.get_northbeam_ad_window(1, limit=20)
    nb_ads_7d = db.get_northbeam_ad_window(7, limit=20)

    # Pinterest ad set (ad group) data
    ag_yesterday = db.get_ad_group_window(1)
    ag_7d        = db.get_ad_group_window(7)

    context = {
        "date":        date.today().isoformat(),
        "target_roas": config.TARGET_ROAS,

        # Primary attribution source
        "northbeam_account":   nb_account,
        "northbeam_campaigns": nb_campaigns,
        "northbeam_ads": {
            "1d": nb_ads_1d,
            "7d": nb_ads_7d,
        },

        # Pinterest platform metrics (delivery / efficiency)
        "pinterest_platform": {
            "yesterday": yesterday_platform,
            "7d_baseline": baseline_7_platform,
        },

        # Ad set level (Pinterest platform)
        "ad_sets": {
            "yesterday": ag_yesterday,
            "7d":        ag_7d,
        },

        # Actions and events
        "graduations_overnight": graduation_events,
        "budget_decisions": [
            {k: v for k, v in d.items() if k not in ("shadow_mode",)}
            for d in budget_decisions
        ],
        "ads_paused": paused_ads,
        "anomalies": [
            a for a in anomaly_result.get("anomalies", [])
            if a.get("priority") in ("P0", "P1")
        ],
        "anomaly_summary":      anomaly_result.get("summary", ""),
        "orchestrator_actions": orchestrator_actions or [],
        "calibration_summary":  calibration_summary,
        "opportunity_report":   opportunity_report,
    }

    user_message = (
        "Write today's morning digest based on the data below.\n\n"
        f"```json\n{json.dumps(context, indent=2, default=str)}\n```"
    )

    log.info("Calling narrative agent...")
    response = _client.messages.create(
        model=config.CLAUDE_MODEL,
        max_tokens=3000,
        system=_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_message}],
    )

    digest = response.content[0].text.strip()
    log.info("Narrative agent complete (%d chars).", len(digest))
    return digest
