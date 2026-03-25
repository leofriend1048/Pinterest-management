"""
Ad-level pause auditor.

Scans every active ad across the entire account and pauses those that hit
any of the two underperformance conditions.

All rules consider performance across three lookback windows:
  yesterday (1d) / last 3 days / last 7 days

  Rule 1 — ROAS decay:
    7-day ROAS dropped > 30% from the ad's first-week ROAS
    AND absolute ROAS is below floor in both 1d AND 3d windows.
    Prevents pausing on a transient 7-day blip if today is recovering.

  Rule 2 — Persistent underperformance:
    ROAS below floor in BOTH 1d AND 3d windows (not just one bad day)
    AND 7d ROAS also below floor
    AND spend > $150 AND runtime > 72 hours.

A creative can be paused in one scale lane while staying active in others.
"""

import logging
from datetime import date, timedelta

import config
from connectors import pinterest_connector as pinterest
from notifications import slack
from state import db

log = logging.getLogger(__name__)

_WINDOWS = (1, 3, 7)


# ── Multi-window analytics fetch ──────────────────────────────────────────────

def _fetch_multiwindow_ad_analytics(
    ad_ids: list[str],
    end: date,
) -> dict[int, dict[str, dict]]:
    """
    Fetch Pinterest ad analytics for 1d, 3d, 7d windows.

    Returns {days: {ad_id: analytics_dict}}
    """
    result: dict[int, dict[str, dict]] = {}
    for days in _WINDOWS:
        start = end - timedelta(days=days - 1)
        try:
            rows = pinterest.get_ad_analytics(
                ad_ids, start.isoformat(), end.isoformat()
            )
            result[days] = {r["ad_id"]: r for r in rows}
        except Exception as exc:
            log.warning("Pinterest %dd ad analytics failed: %s", days, exc)
            result[days] = {}
    return result


# ── DB lookups ────────────────────────────────────────────────────────────────

def _get_first_week_roas(ad_id: str) -> float | None:
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
        return (date.today() - date.fromisoformat(row["first"])).days
    except ValueError:
        return 0


# ── Pause logic ───────────────────────────────────────────────────────────────

def _check_pause_rules(
    ad_id: str,
    brand: str,
    windows: dict[int, dict[str, dict]],   # {days: analytics_dict_for_this_ad}
) -> tuple[bool, str]:
    """
    Evaluate all three pause rules using multi-window data.
    Returns (should_pause, reason).
    """
    target      = config.TARGET_ROAS[brand]
    floor_abs   = target * config.AD_PAUSE_ROAS_FLOOR

    roas_1d = windows.get(1, {}).get("roas", 0.0)
    roas_3d = windows.get(3, {}).get("roas", 0.0)
    roas_7d = windows.get(7, {}).get("roas", 0.0)
    spend_7d    = windows.get(7, {}).get("spend", 0.0)

    # Helper: is this window data present?
    has_1d = roas_1d > 0
    has_3d = roas_3d > 0
    has_7d = roas_7d > 0

    window_str = (
        f"1d={roas_1d:.2f}x | 3d={roas_3d:.2f}x | 7d={roas_7d:.2f}x "
        f"(floor={floor_abs:.2f}x)"
    )

    # ── Rule 1: Persistent underperformance across all three windows ──────────
    runtime = _get_ad_runtime_days(ad_id)
    if runtime > config.AD_PAUSE_MIN_RUNTIME_DAYS and spend_7d > config.AD_PAUSE_MIN_SPEND_USD:
        # All three windows must be present and below floor
        all_bad = (
            has_1d and roas_1d < floor_abs
            and has_3d and roas_3d < floor_abs
            and has_7d and roas_7d < floor_abs
        )
        if all_bad:
            return True, (
                f"Persistent underperformance across all 3 windows. {window_str} "
                f"| spend(7d)=${spend_7d:.0f}, runtime={runtime}d."
            )

        # Softer case: 1d and 3d both bad but 7d not yet below floor — nudge flag only
        if (
            has_1d and roas_1d < floor_abs
            and has_3d and roas_3d < floor_abs
            and spend_7d > config.AD_PAUSE_MIN_SPEND_USD * 2
        ):
            log.info(
                "[%s] Ad %s: 1d+3d below floor but 7d OK — monitoring, not pausing. %s",
                brand, ad_id, window_str,
            )

    # ── Rule 2: ROAS decay (first-week baseline vs recent) ────────────────────
    first_week_roas = _get_first_week_roas(ad_id)
    if first_week_roas and first_week_roas > 0:
        # Use 7d as the "current" end, but require 1d AND 3d to also be weak
        decay = (first_week_roas - roas_7d) / first_week_roas if has_7d else 0.0
        if decay > config.AD_PAUSE_DECAY_THRESHOLD:
            recent_confirmed = (
                (has_1d and roas_1d < floor_abs)
                and (has_3d and roas_3d < floor_abs)
            )
            if recent_confirmed:
                return True, (
                    f"ROAS decayed {decay*100:.0f}% from first-week baseline "
                    f"({first_week_roas:.2f}x) and confirmed weak in recent windows. "
                    f"{window_str}"
                )

    return False, ""


# ── Main entry point ──────────────────────────────────────────────────────────

def run_ad_pause_audit() -> list[dict]:
    """
    Scans every active ad across all brands and campaigns.
    Returns list of pause event dicts for the morning digest.
    """
    yesterday = date.today() - timedelta(days=1)
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

            ad_ids   = [a["id"] for a in active_ads]
            name_map = {a["id"]: a.get("name", a["id"]) for a in active_ads}

            # Fetch 1d, 3d, 7d analytics for all ads in this campaign at once
            mw_raw = _fetch_multiwindow_ad_analytics(ad_ids, yesterday)

            for ad_id in ad_ids:
                # Reshape to {days: analytics_for_this_ad}
                ad_windows = {
                    days: mw_raw.get(days, {}).get(ad_id, {})
                    for days in _WINDOWS
                }

                should_pause, reason = _check_pause_rules(ad_id, brand, ad_windows)
                if not should_pause:
                    continue

                ad_name  = name_map.get(ad_id, ad_id)
                spend_7d = ad_windows.get(7, {}).get("spend", 0.0)
                roas_w   = ad_windows.get(7, {}).get("roas", 0.0)   # 7d for display

                log.info(
                    "[%s/%s] Pausing ad '%s' — %s",
                    brand, camp["campaign_type"], ad_name, reason,
                )

                if not config.SHADOW_MODE:
                    try:
                        pinterest.pause_ad(ad_id)
                    except Exception as exc:
                        log.error("Failed to pause ad %s: %s", ad_id, exc)
                        continue

                # Persist 7d snapshot
                db.insert_performance_snapshot(
                    {
                        "snapshot_date": date.today().isoformat(),
                        "campaign_id":   camp["campaign_id"],
                        "ad_id":         ad_id,
                        "brand":         brand,
                        "campaign_type": camp["campaign_type"],
                        "spend_usd":     spend_7d,
                        "revenue_usd":   roas_w * spend_7d,
                        "roas":          roas_w,
                        "ncpa":          ad_windows.get(7, {}).get("ncpa", 0),
                        "impressions":   ad_windows.get(7, {}).get("impressions", 0),
                        "clicks":        ad_windows.get(7, {}).get("clicks", 0),
                        "ctr":           ad_windows.get(7, {}).get("ctr", 0),
                        "frequency":     ad_windows.get(7, {}).get("frequency", 0),
                        "conversions":   ad_windows.get(7, {}).get("conversions", 0),
                    }
                )

                event = {
                    "ad_id":         ad_id,
                    "ad_name":       ad_name,
                    "brand":         brand,
                    "campaign_type": camp["campaign_type"],
                    "spend_usd":     spend_7d,
                    "roas":          roas_w,
                    "roas_1d":       ad_windows.get(1, {}).get("roas", 0.0),
                    "roas_3d":       ad_windows.get(3, {}).get("roas", 0.0),
                    "roas_7d":       roas_w,
                    "reason":        reason,
                }
                paused_events.append(event)

                slack.alert_ad_paused(
                    ad_id=ad_id,
                    ad_name=ad_name,
                    campaign_type=camp["campaign_type"],
                    brand=brand,
                    spend_usd=spend_7d,
                    roas=roas_w,
                    reason=reason,
                    roas_1d=event["roas_1d"],
                    roas_3d=event["roas_3d"],
                    roas_7d=event["roas_7d"],
                )

    log.info("Ad pause audit complete — %d ads paused.", len(paused_events))
    return paused_events
