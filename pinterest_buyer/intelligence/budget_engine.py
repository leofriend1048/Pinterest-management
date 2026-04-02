"""
Budget allocation engine.

5-tier ROAS-based logic applied once per campaign per day.

Tier  | ROAS vs target  | Action          | Cap
------+-----------------+-----------------+---------
  1   | ≥ 130%          | Scale UP 25%    | $500/day
  2   | 110–129%        | Nudge UP 10%    | $200/day
  3   | 90–109%         | Hold            | —
  4   | 70–89%          | Nudge DOWN 15%  | —
  5   | < 70%           | Pause campaign  | logged

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
    Weighted composite: yesterday 20%, 3-day 30%, 7-day 50%.
    Windows with zero ROAS (no data) are excluded and weights renormalised.
    """
    pairs = [(roas_1d, 0.20), (roas_3d, 0.30), (roas_7d, 0.50)]
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


# ── Data-gap helpers ──────────────────────────────────────────────────────────

def _fire_data_gap_alert(
    brand: str,
    campaign: dict,
    campaign_id: str,
    spend_1d: float,
    nb_present: bool,
) -> None:
    if nb_present:
        root_cause = (
            f"Campaign {campaign_id} IS in Northbeam's export but attributed revenue "
            "is 0.0 across all windows. Likely causes: Pinterest checkout pixel stopped "
            "firing, UTM parameters changed/missing, or Northbeam attribution model "
            "disconnected from this campaign's conversions."
        )
        recommended_action = (
            "1) Open Northbeam → verify campaign is receiving conversion events. "
            "2) Check Pinterest tag diagnostics for checkout pixel fires. "
            "3) Confirm UTM parameters on ad URLs match Northbeam's source mapping. "
            "Budget held until attribution is restored."
        )
    else:
        root_cause = (
            f"Campaign {campaign_id} is NOT present in Northbeam's data export at all "
            "and Pinterest native also shows 0 attributed revenue. Campaign may have been "
            "recently cloned/re-created causing an ID linkage break in Northbeam, or "
            "the Pinterest checkout pixel is not firing for this campaign."
        )
        recommended_action = (
            "1) In Northbeam, search for this campaign by name and verify it is linked "
            f"to campaign ID {campaign_id}. If missing, re-connect it. "
            "2) Check Pinterest tag for checkout events on this campaign. "
            "3) Cross-check Shopify/GA4 for actual sales attributed to this campaign. "
            "Budget held until attribution is confirmed."
        )
    log.warning(
        "[%s/%s] DATA GAP on campaign %s — $%.0f spend, 0.0x ROAS from %s. %s",
        brand, campaign["campaign_type"], campaign_id, spend_1d,
        "Northbeam (present, 0 revenue)" if nb_present else "Northbeam (absent) + Pinterest native",
        root_cause,
    )
    slack.alert_p0_anomaly(
        brand=brand,
        what_changed=(
            "Attribution data gap — 0.0x ROAS across all windows despite active spend "
            f"({'Northbeam present but 0 revenue' if nb_present else 'campaign missing from Northbeam export'})"
        ),
        magnitude=f"${spend_1d:.0f}/day spend, 0.0x ROAS (1d/3d/7d)",
        root_cause=root_cause,
        recommended_action=recommended_action,
    )


def _data_gap_hold(
    campaign: dict,
    campaign_id: str,
    brand: str,
    current_budget: float,
    target_roas: float,
    spend_1d: float,
    nb_present: bool,
) -> dict:
    rationale = (
        f"DATA GAP ({'Northbeam present, 0 revenue' if nb_present else 'campaign absent from Northbeam'}) "
        f"— ${spend_1d:.0f} spent yesterday, 0.0x ROAS all windows. "
        "Budget held until attribution is confirmed."
    )
    return {
        "campaign_id":      campaign_id,
        "brand":            brand,
        "campaign_type":    campaign["campaign_type"],
        "prior_budget_usd": current_budget,
        "new_budget_usd":   current_budget,
        "roas_basis":       0.0,
        "roas_1d":          0.0,
        "roas_3d":          0.0,
        "roas_7d":          0.0,
        "target_roas":      target_roas,
        "roas_ratio":       0.0,
        "action":           "hold",
        "rationale":        rationale,
    }


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

    # ── Data-gap guard ────────────────────────────────────────────────────────
    # Distinguish three cases before proceeding:
    #   A) Campaign absent from Northbeam export entirely (ID not in response)
    #      → fall back to Pinterest native; only alert if Pinterest is also 0
    #   B) Campaign present in Northbeam but all windows are 0.0 (pixel/UTM issue)
    #      → alert immediately; Pinterest native may still be usable
    #   C) Both sources are 0 despite real spend → P0 alert, hold budget
    nb_present = any(
        pin_id in nb_windows.get(d, {}) for d in _WINDOWS
    )
    pin_roas_1d = platform_windows.get(1, {}).get(pin_id, {}).get("roas", 0.0)
    pin_roas_3d = platform_windows.get(3, {}).get(pin_id, {}).get("roas", 0.0)
    pin_roas_7d = platform_windows.get(7, {}).get(pin_id, {}).get("roas", 0.0)

    if spend_1d >= config.MIN_SPEND_FOR_DECISION and roas_1d == 0.0 and roas_3d == 0.0 and roas_7d == 0.0:
        if not nb_present:
            # Case A: campaign simply not in Northbeam export — log a warning
            # but allow Pinterest native ROAS to drive the decision if it has data.
            log.warning(
                "[%s/%s] Campaign %s not found in Northbeam export — "
                "using Pinterest native ROAS only (1d=%.2fx 3d=%.2fx 7d=%.2fx).",
                brand, campaign["campaign_type"], campaign_id,
                pin_roas_1d, pin_roas_3d, pin_roas_7d,
            )
            if pin_roas_1d > 0 or pin_roas_3d > 0 or pin_roas_7d > 0:
                # Pinterest native has data — proceed with that, no alert needed
                pass  # falls through to normal tier logic below
            else:
                # Case C: absent from Northbeam AND Pinterest native also shows 0
                _fire_data_gap_alert(brand, campaign, campaign_id, spend_1d,
                                     nb_present=False)
                return _data_gap_hold(campaign, campaign_id, brand, current_budget,
                                      target_roas, spend_1d, nb_present=False)
        else:
            # Case B/C: Northbeam has the campaign but ROAS is 0 across all windows
            _fire_data_gap_alert(brand, campaign, campaign_id, spend_1d,
                                 nb_present=True)
            return _data_gap_hold(campaign, campaign_id, brand, current_budget,
                                  target_roas, spend_1d, nb_present=True)
    # ─────────────────────────────────────────────────────────────────────────

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
        # Marginal profit check: would a 10% spend increase add net profit
        # while keeping estimated ROAS at or above TARGET_ROAS?
        estimated_roas = w_roas * (1 - config.MARGINAL_ROAS_COMPRESSION)
        proposed_budget = current_budget * (1 + config.BUDGET_SCALE_UP_LIGHT_PCT)
        current_profit_proxy  = w_roas * current_budget
        proposed_profit_proxy = estimated_roas * proposed_budget
        if (
            estimated_roas >= target_roas
            and proposed_profit_proxy > current_profit_proxy
            and proposed_budget <= config.BUDGET_SCALE_UP_LIGHT_CAP
        ):
            new_budget = max(
                min(proposed_budget, config.BUDGET_SCALE_UP_LIGHT_CAP),
                floor,
            )
            action.update(
                action="nudge_up",
                new_budget_usd=round(new_budget, 2),
                rationale=(
                    f"{window_str} — hold zone but marginal profit positive "
                    f"(est. ROAS at +10% spend: {estimated_roas:.2f}x ≥ floor {target_roas:.2f}x; "
                    f"revenue proxy ${proposed_profit_proxy:.0f} vs ${current_profit_proxy:.0f}); "
                    f"nudging UP 10% to ${new_budget:.0f}/day."
                ),
            )
        # else: hold — marginal dollars would drop below floor or not add profit

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

        elif action["action"] == "pause":
            pinterest.pause_campaign(pin_id)
            db.pause_campaign(campaign_id)
            db.log_decision(action)

        else:  # hold
            db.log_decision(action)

        log.info(
            "[%s/%s] %s — 1d=%.2fx 3d=%.2fx 7d=%.2fx weighted=%.2fx | $%.0f→$%.0f",
            camp["brand"], camp["campaign_type"], action["action"].upper(),
            action["roas_1d"], action["roas_3d"], action["roas_7d"], action["roas_basis"],
            action["prior_budget_usd"], action["new_budget_usd"],
        )

    return actions
