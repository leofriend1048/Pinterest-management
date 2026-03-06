"""
Ad-level pause auditor.

Scans every active ad across the entire account and pauses those that hit
any of the three underperformance conditions:

  Rule 1 — Frequency cap:
    frequency > 3.5 AND ROAS < target  (high freq + bad ROAS)

  Rule 2 — ROAS decay:
    7-day ROAS dropped > 30% from its first-week ROAS
    AND absolute ROAS < 0.7× target

  Rule 3 — Persistent underperformance:
    ROAS < 0.7× target AND spend > $150 AND runtime > 72 hours

A creative can be paused in one scale lane while staying active in others.
"""

import logging
from datetime import date, timedelta

import config
from connectors import pinterest_connector as pinterest
from notifications import slack
from state import db

log = logging.getLogger(__name__)


def _get_first_week_roas(ad_id: str) -> float | None:
    """Return the average ROAS from the first 7 days of this ad's life."""
    with db.get_db() as conn:
        row = conn.execute(
            """SELECT AVG(roas) as roas
               FROM performance_snapshots
               WHERE ad_id = ?
                 AND snapshot_date BETWEEN
                     (SELECT MIN(snapshot_date) FROM performance_snapshots WHERE ad_id=?)
                     AND date(
                         (SELECT MIN(snapshot_date) FROM performance_snapshots WHERE ad_id=?),
                         '+7 days'
                     )""",
            (ad_id, ad_id, ad_id)
        ).fetchone()
    return row["roas"] if row and row["roas"] is not None else None


def _get_ad_runtime_days(ad_id: str) -> int:
    with db.get_db() as conn:
        row = conn.execute(
            "SELECT MIN(snapshot_date) as first FROM performance_snapshots WHERE ad_id=?",
            (ad_id,)
        ).fetchone()
    if not row or not row["first"]:
        return 0
    try:
        first = date.fromisoformat(row["first"])
        return (date.today() - first).days
    except ValueError:
        return 0


def _check_pause_rules(
    ad: dict,
    brand: str,
    recent_roas: float,
    spend: float,
    frequency: float,
) -> tuple[bool, str]:
    """
    Returns (should_pause, reason).
    """
    target = config.TARGET_ROAS[brand]

    # Rule 1: Frequency + ROAS
    if (
        frequency > config.AD_PAUSE_FREQUENCY_THRESHOLD
        and recent_roas < target * config.AD_PAUSE_ROAS_FLOOR
    ):
        return True, (
            f"Frequency {frequency:.1f} > {config.AD_PAUSE_FREQUENCY_THRESHOLD} "
            f"AND ROAS {recent_roas:.2f}x < {target * config.AD_PAUSE_ROAS_FLOOR:.2f}x target floor."
        )

    # Rule 3: Persistent underperformance (check before decay so runtime gate applies to both)
    runtime = _get_ad_runtime_days(ad.get("ad_id", ""))
    if (
        recent_roas < target * config.AD_PAUSE_ROAS_FLOOR
        and spend > config.AD_PAUSE_MIN_SPEND_USD
        and runtime > config.AD_PAUSE_MIN_RUNTIME_DAYS
    ):
        return True, (
            f"ROAS {recent_roas:.2f}x < {target * config.AD_PAUSE_ROAS_FLOOR:.2f}x target, "
            f"spend ${spend:.0f} > ${config.AD_PAUSE_MIN_SPEND_USD:.0f}, "
            f"runtime {runtime}d > {config.AD_PAUSE_MIN_RUNTIME_DAYS}d."
        )

    # Rule 2: ROAS decay
    first_week_roas = _get_first_week_roas(ad.get("ad_id", ""))
    if first_week_roas and first_week_roas > 0:
        decay = (first_week_roas - recent_roas) / first_week_roas
        if (
            decay > config.AD_PAUSE_DECAY_THRESHOLD
            and recent_roas < target * config.AD_PAUSE_ROAS_FLOOR
        ):
            return True, (
                f"7-day ROAS decayed {decay*100:.0f}% from first-week baseline "
                f"({first_week_roas:.2f}x → {recent_roas:.2f}x) "
                f"AND absolute ROAS < {target * config.AD_PAUSE_ROAS_FLOOR:.2f}x target floor."
            )

    return False, ""


def run_ad_pause_audit() -> list[dict]:
    """
    Main entry point. Runs for all active campaigns across all brands.
    Returns list of pause event dicts for the morning digest.
    """
    end   = date.today() - timedelta(days=1)
    start = end - timedelta(days=6)  # 7-day window

    paused_events: list[dict] = []

    for brand in config.BRANDS:
        campaigns = db.get_active_campaigns(brand)
        if not campaigns:
            continue

        for camp in campaigns:
            campaign_id = camp["pinterest_campaign_id"] or camp["campaign_id"]
            ads = pinterest.list_ads_in_campaign(campaign_id)
            active_ads = [a for a in ads if a.get("status") == "ACTIVE"]
            if not active_ads:
                continue

            ad_ids = [a["id"] for a in active_ads]
            try:
                analytics = pinterest.get_ad_analytics(
                    ad_ids, start.isoformat(), end.isoformat()
                )
            except Exception as exc:
                log.error("Analytics fetch failed for campaign %s: %s", campaign_id, exc)
                continue

            name_map = {a["id"]: a.get("name", a["id"]) for a in active_ads}

            for row in analytics:
                ad_id     = row.get("ad_id", "")
                ad_name   = name_map.get(ad_id, ad_id)
                spend     = row.get("spend", 0)
                roas      = row.get("roas", 0)
                frequency = row.get("frequency", 0)

                should_pause, reason = _check_pause_rules(
                    row, brand, roas, spend, frequency
                )

                if not should_pause:
                    continue

                log.info(
                    "[%s/%s] Pausing ad %s (ROAS=%.2f, spend=$%.0f, freq=%.1f): %s",
                    brand, camp["campaign_type"], ad_name, roas, spend, frequency, reason,
                )

                if not config.SHADOW_MODE:
                    try:
                        pinterest.pause_ad(ad_id)
                    except Exception as exc:
                        log.error("Failed to pause ad %s: %s", ad_id, exc)
                        continue

                # Persist snapshot
                db.insert_performance_snapshot(
                    {
                        "snapshot_date": date.today().isoformat(),
                        "campaign_id":   camp["campaign_id"],
                        "ad_id":         ad_id,
                        "brand":         brand,
                        "campaign_type": camp["campaign_type"],
                        "spend_usd":     spend,
                        "revenue_usd":   roas * spend,
                        "roas":          roas,
                        "ncpa":          row.get("ncpa", 0),
                        "impressions":   row.get("impressions", 0),
                        "clicks":        row.get("clicks", 0),
                        "ctr":           row.get("ctr", 0),
                        "frequency":     frequency,
                        "conversions":   row.get("conversions", 0),
                    }
                )

                event = {
                    "ad_id":         ad_id,
                    "ad_name":       ad_name,
                    "brand":         brand,
                    "campaign_type": camp["campaign_type"],
                    "spend_usd":     spend,
                    "roas":          roas,
                    "reason":        reason,
                }
                paused_events.append(event)

                slack.alert_ad_paused(
                    ad_id=ad_id,
                    ad_name=ad_name,
                    campaign_type=camp["campaign_type"],
                    brand=brand,
                    spend_usd=spend,
                    roas=roas,
                    reason=reason,
                )

    log.info("Ad pause audit complete — %d ads paused.", len(paused_events))
    return paused_events
