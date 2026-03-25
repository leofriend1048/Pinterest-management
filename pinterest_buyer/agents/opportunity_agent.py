"""
Opportunity agent — runs weekly on Mondays.

Surfaces:
  1. Meta creatives with traction not yet on Pinterest
  2. Scale lane divergence — which targeting type is winning per brand
  3. Keyword expansion recommendations
  4. Cross-brand angle opportunities
  5. Format-level wins worth concentrating
"""

import json
import logging
from datetime import date

import anthropic

import config
from intelligence.fatigue_detector import (
    check_cross_brand_opportunities,
    get_daypart_recommendations,
    get_format_performance,
)
from state import db

log = logging.getLogger(__name__)
_client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)


_SYSTEM_PROMPT = """You are a senior DTC performance buyer writing a weekly opportunity briefing
for Pinterest ad accounts across three brands: Michael Todd Beauty, NasalFresh MD, and Spa Sciences.

You will receive a structured JSON payload with:
  - Scale lane divergence data (which targeting type is winning per brand)
  - Keyword performance and expansion candidates
  - Cross-brand angle opportunities
  - Creative format performance
  - Dayparting efficiency data
  - Meta creatives not yet mirrored to Pinterest

Write a concise, actionable weekly opportunity update. For each section:
  - State the finding clearly
  - Give the supporting numbers
  - Make a specific recommendation

Tone: direct, strategic, no filler. This replaces Leo checking the account manually.
Target: 300–500 words. Use headers for each section."""


def _get_scale_lane_divergence() -> list[dict]:
    """Compare ROAS across scale campaign types per brand."""
    divergence = []
    for brand in config.BRANDS:
        campaigns = db.get_active_campaigns(brand)
        scale_camps = [c for c in campaigns if c["campaign_type"] in config.SCALE_CAMPAIGN_TYPES]

        rows = db.get_performance_window(brand, days=14)
        roas_by_type: dict[str, list[float]] = {}
        for r in rows:
            ct = r.get("campaign_type")
            if ct in config.SCALE_CAMPAIGN_TYPES and r.get("roas"):
                roas_by_type.setdefault(ct, []).append(r["roas"])

        avg_by_type = {ct: sum(v)/len(v) for ct, v in roas_by_type.items() if v}
        if avg_by_type:
            best = max(avg_by_type, key=avg_by_type.get)
            worst = min(avg_by_type, key=avg_by_type.get)
            divergence.append(
                {
                    "brand":         brand,
                    "roas_by_type":  avg_by_type,
                    "best_type":     best,
                    "best_roas":     avg_by_type[best],
                    "worst_type":    worst,
                    "worst_roas":    avg_by_type[worst],
                    "spread":        avg_by_type[best] - avg_by_type[worst],
                }
            )
    return divergence


def _get_keyword_candidates() -> list[dict]:
    """Surface keywords driving spend but not yet in KEYWORD_SEEDS."""
    with db.get_db() as conn:
        rows = conn.execute(
            """SELECT brand, keyword, SUM(impressions) as imps,
                      SUM(conversions) as convs, AVG(roas) as roas
               FROM keyword_performance
               WHERE snapshot_date >= date('now', '-30 days')
               GROUP BY brand, keyword
               HAVING SUM(impressions) > 500
               ORDER BY roas DESC"""
        ).fetchall()

    candidates = []
    for r in rows:
        seeds = config.KEYWORD_SEEDS.get(r["brand"], [])
        if r["keyword"] not in seeds:
            candidates.append(dict(r))

    return candidates[:20]  # top 20 expansion candidates


def _get_unmirrored_meta_creatives() -> list[dict]:
    """Return Meta creatives with spend but not yet on Pinterest."""
    with db.get_db() as conn:
        rows = conn.execute(
            """SELECT brand, meta_creative_id, meta_spend_usd, meta_roas,
                      composite_score, creative_format, graduation_status
               FROM creative_mirror
               WHERE graduation_status = 'pending'
                 AND meta_spend_usd >= ?
               ORDER BY composite_score DESC
               LIMIT 20""",
            (config.META_CREATIVE_MIN_SPEND_USD,)
        ).fetchall()
    return [dict(r) for r in rows]


def run_opportunity_analysis() -> str:
    """
    Main entry point — runs on Mondays.
    Returns the opportunity report text and posts it to Slack.
    """
    log.info("Running weekly opportunity agent...")

    payload = {
        "date":                       date.today().isoformat(),
        "brands":                     config.BRANDS,
        "target_roas":                config.TARGET_ROAS,
        "scale_lane_divergence":      _get_scale_lane_divergence(),
        "keyword_expansion_candidates": _get_keyword_candidates(),
        "unmirrored_meta_creatives":  _get_unmirrored_meta_creatives(),
        "cross_brand_opportunities":  check_cross_brand_opportunities(),
        "format_performance":         {
            brand: get_format_performance(brand) for brand in config.BRANDS
        },
        "daypart_recommendations":    {
            brand: get_daypart_recommendations(brand) for brand in config.BRANDS
        },
    }

    user_message = (
        "Write this week's Pinterest opportunity update based on the data below.\n\n"
        f"```json\n{json.dumps(payload, indent=2, default=str)}\n```"
    )

    response = _client.messages.create(
        model=config.CLAUDE_MODEL,
        max_tokens=2048,
        system=_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_message}],
    )

    report = response.content[0].text.strip()
    log.info("Opportunity agent complete (%d chars).", len(report))
    return report
