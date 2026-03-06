"""
Creative fatigue detector.

Tracks dayparting efficiency and format-level performance.

Dayparting
----------
Logs spend + revenue by hour of day per brand.
Once 200+ hourly data points exist, surfaces peak-efficiency windows
in the weekly Slack update (advisory only — no automated scheduling).

Format concentration
--------------------
Every 30 days, identifies the highest- and lowest-performing creative
format by ROAS and returns a recommendation dict for the opportunity agent.
"""

import logging
from datetime import date, timedelta
from collections import defaultdict

import config
from state import db

log = logging.getLogger(__name__)


# ── Dayparting ────────────────────────────────────────────────────────────────

def ingest_daypart_data(brand: str, hourly_data: list[dict]) -> None:
    """
    Persist hourly spend/revenue data into daypart_log.

    hourly_data: [{"hour_utc": int, "spend_usd": float, "revenue_usd": float}]
    """
    today = date.today().isoformat()
    for row in hourly_data:
        db.upsert_daypart(
            {
                "log_date":    today,
                "hour_utc":    row["hour_utc"],
                "brand":       brand,
                "spend_usd":   row.get("spend_usd", 0),
                "revenue_usd": row.get("revenue_usd", 0),
            }
        )


def get_daypart_recommendations(brand: str) -> dict | None:
    """
    Return peak-efficiency hours if sufficient data exists, else None.

    Returns:
    {
        "brand": str,
        "peak_hours_utc": list[int],
        "off_peak_hours_utc": list[int],
        "avg_roas_by_hour": dict,
        "data_points": int,
    }
    """
    rows = db.get_daypart_data(brand, rolling_days=config.DAYPART_ROLLING_DAYS)
    total_points = sum(r["data_points"] for r in rows)

    if total_points < config.DAYPART_MIN_DATA_POINTS:
        log.debug("[%s] Daypart data insufficient (%d points).", brand, total_points)
        return None

    roas_by_hour = {r["hour_utc"]: r["avg_roas"] for r in rows}
    if not roas_by_hour:
        return None

    median_roas = sorted(roas_by_hour.values())[len(roas_by_hour) // 2]
    peak_hours     = [h for h, r in roas_by_hour.items() if r >= median_roas * 1.15]
    off_peak_hours = [h for h, r in roas_by_hour.items() if r <= median_roas * 0.85]

    return {
        "brand":             brand,
        "peak_hours_utc":    sorted(peak_hours),
        "off_peak_hours_utc": sorted(off_peak_hours),
        "avg_roas_by_hour":  roas_by_hour,
        "data_points":       total_points,
    }


# ── Format concentration ──────────────────────────────────────────────────────

def get_format_performance(brand: str, lookback_days: int = 30) -> dict | None:
    """
    Analyse 30-day ROAS by creative_format (video/image) for a brand.

    Returns:
    {
        "brand": str,
        "formats": {"video": {"roas": float, "spend": float}, "image": {...}},
        "top_format": str,
        "bottom_format": str,
        "recommendation": str,
    }
    """
    end   = date.today() - timedelta(days=1)
    start = end - timedelta(days=lookback_days - 1)

    with db.get_db() as conn:
        rows = conn.execute(
            """SELECT cm.creative_format,
                      SUM(ps.spend_usd)   as total_spend,
                      AVG(ps.roas)        as avg_roas,
                      COUNT(*)            as ad_count
               FROM performance_snapshots ps
               JOIN creative_mirror cm
                 ON ps.ad_id = json_extract(cm.pinterest_pin_ids, '$.*')
               WHERE ps.brand = ?
                 AND ps.snapshot_date BETWEEN ? AND ?
               GROUP BY cm.creative_format""",
            (brand, start.isoformat(), end.isoformat())
        ).fetchall()

    if not rows:
        return None

    formats: dict[str, dict] = {}
    for r in rows:
        if r["creative_format"]:
            formats[r["creative_format"]] = {
                "roas":     r["avg_roas"] or 0.0,
                "spend":    r["total_spend"] or 0.0,
                "ad_count": r["ad_count"],
            }

    if len(formats) < 2:
        return None

    top_fmt    = max(formats, key=lambda f: formats[f]["roas"])
    bottom_fmt = min(formats, key=lambda f: formats[f]["roas"])
    top_roas   = formats[top_fmt]["roas"]
    bot_roas   = formats[bottom_fmt]["roas"]

    recommendation = (
        f"{top_fmt.title()} is outperforming {bottom_fmt} "
        f"({top_roas:.2f}x vs {bot_roas:.2f}x ROAS over 30 days). "
        f"Recommend +{config.FORMAT_BUDGET_BOOST_PCT*100:.0f}% budget "
        f"weight toward {top_fmt} creatives. "
        f"{bottom_fmt.title()} creative pool should be reviewed."
    )

    return {
        "brand":          brand,
        "formats":        formats,
        "top_format":     top_fmt,
        "bottom_format":  bottom_fmt,
        "recommendation": recommendation,
    }


# ── Cross-brand angle flag ────────────────────────────────────────────────────

def check_cross_brand_opportunities() -> list[dict]:
    """
    When one brand's creative outperforms its 30-day baseline by 1.5×+,
    flag the angle for the other two brands.

    Returns list of opportunity dicts for the opportunity agent.
    """
    opportunities: list[dict] = []

    for brand in config.BRANDS:
        baseline_rows = db.get_baseline_summary(days=30)
        baseline_map  = {
            (r["brand"], r["campaign_type"]): r["avg_roas"]
            for r in baseline_rows
        }

        yesterday_rows = db.get_yesterday_summary()
        for row in yesterday_rows:
            if row["brand"] != brand:
                continue
            key = (brand, row["campaign_type"])
            baseline = baseline_map.get(key)
            if not baseline or baseline <= 0:
                continue

            ratio = row["roas"] / baseline
            if ratio >= config.CROSS_BRAND_OUTPERFORM_X:
                other_brands = [b for b in config.BRANDS if b != brand]
                opportunities.append(
                    {
                        "source_brand":   brand,
                        "campaign_type":  row["campaign_type"],
                        "current_roas":   row["roas"],
                        "baseline_roas":  baseline,
                        "outperform_ratio": ratio,
                        "target_brands":  other_brands,
                        "recommendation": (
                            f"{brand} is running at {ratio:.1f}× its 30-day baseline "
                            f"in {row['campaign_type']}. Check if a similar creative angle "
                            f"exists for {', '.join(other_brands)}."
                        ),
                    }
                )

    return opportunities
