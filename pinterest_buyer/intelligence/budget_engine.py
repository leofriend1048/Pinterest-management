"""
Budget allocation engine.

5-tier ROAS-based logic applied once per campaign per day.

Tier  | ROAS vs target  | Action          | Cap
------+-----------------+-----------------+---------
  1   | ≥ 130%          | Scale UP 25%    | $500/day
  2   | 110–129%        | Nudge UP 10%    | $200/day
  3   | 90–109%         | Hold            | —
  4   | 70–89%          | Nudge DOWN 15%  | —
  5   | < 70%           | Pause campaign  | Slack flag

ROAS signal: weighted across three lookback windows
  yesterday (1d)  50%
  last 3 days     30%
  last 7 days     20%

This prevents a single good or bad day from driving an outsized decision.

Additional guards:
  Scale UP strong  — weighted ROAS ≥ 130% AND yesterday also ≥ 110%
                     (won't scale on stale data if yesterday dipped)
  Pause            — weighted ROAS < 70% AND at least 2 of 3 windows
                     are individually below 70% (no single-day over-reaction)
  Budget floor     — seasonal multipliers prevent cutting below floor
  One-change gate  — one budget decision per campaign per day
  Spend gate       — minimum $50 spend before any decision fires
  Runtime gate     — minimum 72-hour (3-day) runtime
"""

import logging
from datetime import date, timedelta

import config
from connectors import northbeam_connector, pinterest_connector as pinterest
from notifications import slack
from state import db

log = logging.getLogger(__name__)

_WINDOWS = (1, 3, 7)


# ── Multi-window helpers ──────────────────────────────────────────────────────

def _fetch_multiwindow_platform(
    campaign_ids: list[str],
    end: date,
) -> dict[int, dict[str, dict]]:
    """
    Pull Pinterest platform analytics for 1-day, 3-day, and 7-day windows.

    Returns {days: {campaign_id: analytics_dict}}
    """
    result: dict[int, dict[str, dict]] = {}
    for days in _WINDOWS:
        start = end - timedelta(days=days - 1)
        try:
            rows = pinterest.get_campaign_analytics(
                campaign_ids, start.isoformat(), end.isoformat()
            )
            result[days] = {r["campaign_id"]: r for r in rows}
        except Exception as exc:
            log.warning("Pinterest %dd analytics fetch failed: %s", days, exc)
            result[days] = {}
    return result


def _resolve_roas(
    pin_id: str,
    days: int,
    nb_windows: dict[int, dict[str, dict]],
    platform_windows: dict[int, dict[str, dict]],
) -> float:
    """Return Northbeam ROAS for this window; fall back to platform ROAS."""
    nb = nb_windows.get(days, {}).get(pin_id, {}).get("roas", 0.0)
    return nb if nb > 0 else platform_windows.get(days, {}).get(pin_id, {}).get("roas", 0.0)


def _resolve_spend(
    pin_id: str,
    days: int,
    platform_windows: dict[int, dict[str, dict]],
) -> float:
    return platform_windows.get(days, {}).get(pin_id, {}).get("spend", 0.0)


def _weighted_roas(roas_1d: float, roas_3d: float, roas_7d: float) -> float:
    """
    Weighted composite: yesterday 50%, 3-day 30%, 7-day 20%.
    Windows with zero ROAS (no data) are excluded and weights renormalised.
    """
    pairs = [(roas_1d, 0.50), (roas_3d, 0.30), (roas_7d, 0.20)]
    valid = [(r, w) for r, w in pairs if r > 0]
    if not valid:
        return 0.0
    total_w = sum(w for _, w in valid)
    return sum(r * w for r, w in valid) / total_w


def _windows_below_threshold(
    roas_1d: float, roas_3d: float, roas_7d: float, threshold: float
) -> int:
    """Count how many windows are individually below a ROAS threshold."""
    return sum(
        1 for r in (roas_1d, roas_3d, roas_7d) if r > 0 and r < threshold
    )


# ── Seasonal floor ────────────────────────────────────────────────────────────

def _seasonal_floor(brand: str, base_budget: float) -> float:
    month = date.today().month
    multiplier = config.SEASONAL_FLOORS.get(brand, {}).get(month, 1.0)
    return base_budget * multiplier


def _days_since_launch(launch_date_str: str | None) -> int:
    if not launch_date_str:
        return 0
    try:
        return (date.today() - date.fromisoformat(launch_date_str[:10])).days
    except ValueError:
        return 0


# ── Decision logic ────────────────────────────────────────────────────────────

def _decide(
    campaign: dict,
    nb_windows: dict[int, dict[str, dict]],
    platform_windows: dict[int, dict[str, dict]],
) -> dict:
    """
    Determine the budget action for one campaign using multi-window ROAS.

    Returns an action dict with full rationale showing all three windows.
    """
    brand          = campaign["brand"]
    campaign_id    = campaign["campaign_id"]
    pin_id         = campaign.get("pinterest_campaign_id") or campaign_id
    current_budget = campaign["budget_usd"]
    target_roas    = config.TARGET_ROAS[brand]

    roas_1d = _resolve_roas(pin_id, 1, nb_windows, platform_windows)
    roas_3d = _resolve_roas(pin_id, 3, nb_windows, platform_windows)
    roas_7d = _resolve_roas(pin_id, 7, nb_windows, platform_windows)
    spend_1d = _resolve_spend(pin_id, 1, platform_windows)

    w_roas = _weighted_roas(roas_1d, roas_3d, roas_7d)
    ratio  = w_roas / target_roas if target_roas else 0.0

    window_str = (
        f"1d={roas_1d:.2f}x | 3d={roas_3d:.2f}x | 7d={roas_7d:.2f}x "
        f"→ weighted={w_roas:.2f}x vs target={target_roas:.2f}x"
    )

    action = {
        "campaign_id":      campaign_id,
        "brand":            brand,
        "campaign_type":    campaign["campaign_type"],
        "prior_budget_usd": current_budget,
        "new_budget_usd":   current_budget,
        "roas_basis":       w_roas,
        "roas_1d":          roas_1d,
        "roas_3d":          roas_3d,
        "roas_7d":          roas_7d,
        "target_roas":      target_roas,
        "roas_ratio":       ratio,
        "action":           "hold",
        "rationale":        f"{window_str} — hold.",
    }

    floor = _seasonal_floor(brand, current_budget)
    pause_threshold  = target_roas * config.ROAS_PAUSE_CAMPAIGN

    if ratio >= config.ROAS_SCALE_UP_STRONG:
        # Guard: yesterday must also be above nudge-up threshold to prevent
        # scaling on stale multi-day data when yesterday actually dipped.
        if roas_1d > 0 and roas_1d < target_roas * config.ROAS_SCALE_UP_LIGHT:
            action.update(
                action="hold",
                rationale=(
                    f"{window_str} — weighted ROAS qualifies for scale-up but "
                    f"yesterday ({roas_1d:.2f}x) is below 110% of target; holding."
                ),
            )
        else:
            new_budget = max(
                min(
                    current_budget * (1 + config.BUDGET_SCALE_UP_STRONG_PCT),
                    config.BUDGET_SCALE_UP_STRONG_CAP,
                ),
                floor,
            )
            action.update(
                action="scale_up",
                new_budget_usd=round(new_budget, 2),
                rationale=(
                    f"{window_str} — all windows strong, scaling UP 25% "
                    f"to ${new_budget:.0f}/day."
                ),
            )

    elif ratio >= config.ROAS_SCALE_UP_LIGHT:
        new_budget = max(
            min(
                current_budget * (1 + config.BUDGET_SCALE_UP_LIGHT_PCT),
                config.BUDGET_SCALE_UP_LIGHT_CAP,
            ),
            floor,
        )
        action.update(
            action="nudge_up",
            new_budget_usd=round(new_budget, 2),
            rationale=(
                f"{window_str} — weighted ROAS in 110–129% band, "
                f"nudging UP 10% to ${new_budget:.0f}/day."
            ),
        )

    elif ratio >= config.ROAS_HOLD_LOWER:
        pass  # hold, defaults set above

    elif ratio >= config.ROAS_NUDGE_DOWN:
        new_budget = max(
            current_budget * (1 - config.BUDGET_NUDGE_DOWN_PCT),
            floor,
        )
        action.update(
            action="nudge_down",
            new_budget_usd=round(new_budget, 2),
            rationale=(
                f"{window_str} — weighted ROAS in 70–89% band, "
                f"nudging DOWN 15% to ${new_budget:.0f}/day (seasonal floor: ${floor:.0f})."
            ),
        )

    else:
        # Pause guard: require at least 2 of 3 windows to be individually bad.
        # A single catastrophic day (roas_1d only) won't trigger a pause alone.
        bad_windows = _windows_below_threshold(roas_1d, roas_3d, roas_7d, pause_threshold)
        if bad_windows >= 2:
            action.update(
                action="pause",
                new_budget_usd=current_budget,
                rationale=(
                    f"{window_str} — {bad_windows}/3 windows below "
                    f"{pause_threshold:.2f}x pause floor; pausing campaign."
                ),
            )
        else:
            # Weighted avg is bad but only 1 confirmed window — nudge down instead.
            new_budget = max(
                current_budget * (1 - config.BUDGET_NUDGE_DOWN_PCT),
                floor,
            )
            action.update(
                action="nudge_down",
                new_budget_usd=round(new_budget, 2),
                rationale=(
                    f"{window_str} — weighted ROAS below 70% but only "
                    f"{bad_windows}/3 windows confirmed; nudging DOWN 15% "
                    f"to ${new_budget:.0f}/day rather than pausing."
                ),
            )

    return action


# ── Gate helpers ──────────────────────────────────────────────────────────────

def _already_changed_today(campaign_id: str) -> bool:
    today = date.today().isoformat()
    with db.get_db() as conn:
        row = conn.execute(
            """SELECT id FROM decision_audit
               WHERE campaign_id=? AND date(timestamp)=? AND action != 'hold'""",
            (campaign_id, today)
        ).fetchone()
    return row is not None


# ── Main entry point ──────────────────────────────────────────────────────────

def run_budget_optimization() -> list[dict]:
    """
    Runs for all active campaigns across all brands.
    One decision per campaign per day.
    Returns list of action dicts for the morning digest.
    """
    yesterday = date.today() - timedelta(days=1)

    all_campaigns = db.get_active_campaigns()
    if not all_campaigns:
        log.info("No active campaigns to optimize.")
        return []

    all_pin_ids = [
        c["pinterest_campaign_id"] or c["campaign_id"]
        for c in all_campaigns
        if c.get("pinterest_campaign_id") or c.get("campaign_id")
    ]

    # Fetch all three windows from both sources up front
    log.info("Fetching multi-window Northbeam data (1d/3d/7d)...")
    nb_windows = northbeam_connector.fetch_multiwindow_campaign_roas(windows=_WINDOWS)

    log.info("Fetching multi-window Pinterest platform data (1d/3d/7d)...")
    platform_windows = _fetch_multiwindow_platform(all_pin_ids, yesterday)

    actions: list[dict] = []

    for camp in all_campaigns:
        campaign_id = camp["campaign_id"]
        pin_id      = camp.get("pinterest_campaign_id") or campaign_id

        if _already_changed_today(campaign_id):
            log.debug("[%s/%s] Already changed today — skip.", camp["brand"], camp["campaign_type"])
            continue

        # Gate: minimum spend (use yesterday's 1-day window)
        spend_1d = _resolve_spend(pin_id, 1, platform_windows)
        if spend_1d < config.MIN_SPEND_FOR_DECISION:
            log.debug(
                "[%s/%s] Yesterday spend $%.0f below gate — skip.",
                camp["brand"], camp["campaign_type"], spend_1d,
            )
            continue

        # Gate: minimum runtime
        if _days_since_launch(camp.get("launch_date")) < config.MIN_RUNTIME_DAYS:
            log.debug("[%s/%s] Too new — skip.", camp["brand"], camp["campaign_type"])
            continue

        action = _decide(camp, nb_windows, platform_windows)
        actions.append(action)

        if config.SHADOW_MODE:
            log.info(
                "[SHADOW] %s/%s → %s | %s",
                camp["brand"], camp["campaign_type"], action["action"], action["rationale"],
            )
            db.log_decision(action)
            continue

        # Execute
        if action["action"] in ("scale_up", "nudge_up", "nudge_down"):
            pinterest.update_campaign_budget(pin_id, action["new_budget_usd"])
            db.update_campaign_budget(campaign_id, action["new_budget_usd"])
            db.log_decision(action)
            slack.alert_budget_change(
                campaign_id=campaign_id,
                campaign_type=camp["campaign_type"],
                brand=camp["brand"],
                old_budget=action["prior_budget_usd"],
                new_budget=action["new_budget_usd"],
                roas_basis=action["roas_basis"],
                rationale=action["rationale"],
            )

        elif action["action"] == "pause":
            pinterest.pause_campaign(pin_id)
            db.pause_campaign(campaign_id)
            db.log_decision(action)
            slack.alert_campaign_paused(
                campaign_id=campaign_id,
                campaign_type=camp["campaign_type"],
                brand=camp["brand"],
                roas=action["roas_basis"],
                reason=action["rationale"],
            )

        else:  # hold
            db.log_decision(action)

        log.info(
            "[%s/%s] %s — 1d=%.2fx 3d=%.2fx 7d=%.2fx weighted=%.2fx | $%.0f→$%.0f",
            camp["brand"], camp["campaign_type"], action["action"].upper(),
            action["roas_1d"], action["roas_3d"], action["roas_7d"], action["roas_basis"],
            action["prior_budget_usd"], action["new_budget_usd"],
        )

    return actions
