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

Gates before any decision fires:
  - Minimum $50 spend on the campaign
  - Minimum 72-hour (3-day) runtime
  - Seasonal budget floors respected
"""

import logging
from datetime import date, timedelta

import config
from connectors import northbeam_connector, pinterest_connector as pinterest
from notifications import slack
from state import db

log = logging.getLogger(__name__)


def _seasonal_floor(brand: str, base_budget: float) -> float:
    month = date.today().month
    multiplier = config.SEASONAL_FLOORS.get(brand, {}).get(month, 1.0)
    return base_budget * multiplier


def _days_since_launch(launch_date_str: str | None) -> int:
    if not launch_date_str:
        return 0
    try:
        launch = date.fromisoformat(launch_date_str[:10])
        return (date.today() - launch).days
    except ValueError:
        return 0


def _decide(
    campaign: dict,
    northbeam_roas: float,
    platform_roas: float,
) -> dict:
    """
    Determine the action for one campaign. Returns an action dict.
    Uses Northbeam ROAS when available; falls back to platform ROAS.
    """
    roas         = northbeam_roas if northbeam_roas > 0 else platform_roas
    brand        = campaign["brand"]
    campaign_id  = campaign["campaign_id"]
    current_budget = campaign["budget_usd"]
    target_roas  = config.TARGET_ROAS[brand]
    ratio        = roas / target_roas if target_roas else 0

    action = {
        "campaign_id":    campaign_id,
        "brand":          brand,
        "campaign_type":  campaign["campaign_type"],
        "prior_budget_usd": current_budget,
        "new_budget_usd":   current_budget,
        "roas_basis":     roas,
        "target_roas":    target_roas,
        "roas_ratio":     ratio,
        "action":         "hold",
        "rationale":      f"ROAS {roas:.2f}x vs target {target_roas:.2f}x (ratio {ratio:.2f}) — hold.",
    }

    floor = _seasonal_floor(brand, current_budget)

    if ratio >= config.ROAS_SCALE_UP_STRONG:
        new_budget = min(
            current_budget * (1 + config.BUDGET_SCALE_UP_STRONG_PCT),
            config.BUDGET_SCALE_UP_STRONG_CAP,
        )
        new_budget = max(new_budget, floor)
        action.update(
            action="scale_up",
            new_budget_usd=round(new_budget, 2),
            rationale=(
                f"ROAS {roas:.2f}x ≥ 130% of target {target_roas:.2f}x — "
                f"scaling UP 25% to ${new_budget:.0f}/day."
            ),
        )

    elif ratio >= config.ROAS_SCALE_UP_LIGHT:
        new_budget = min(
            current_budget * (1 + config.BUDGET_SCALE_UP_LIGHT_PCT),
            config.BUDGET_SCALE_UP_LIGHT_CAP,
        )
        new_budget = max(new_budget, floor)
        action.update(
            action="nudge_up",
            new_budget_usd=round(new_budget, 2),
            rationale=(
                f"ROAS {roas:.2f}x in 110–129% band — "
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
                f"ROAS {roas:.2f}x in 70–89% band — "
                f"nudging DOWN 15% to ${new_budget:.0f}/day (floor: ${floor:.0f})."
            ),
        )

    else:
        action.update(
            action="pause",
            new_budget_usd=current_budget,
            rationale=(
                f"ROAS {roas:.2f}x < 70% of target {target_roas:.2f}x — "
                f"pausing campaign and flagging."
            ),
        )

    return action


def _already_changed_today(campaign_id: str) -> bool:
    """Return True if a budget change was already logged for this campaign today."""
    today = date.today().isoformat()
    with db.get_db() as conn:
        row = conn.execute(
            """SELECT id FROM decision_audit
               WHERE campaign_id=? AND date(timestamp)=? AND action != 'hold'""",
            (campaign_id, today)
        ).fetchone()
    return row is not None


def run_budget_optimization() -> list[dict]:
    """
    Main entry point — runs for all active campaigns across all brands.
    Applies one decision per campaign per day.
    Returns list of action dicts for the morning digest.
    """
    # Fetch Northbeam ROAS data (platform-filtered, 14-day lookback)
    nb_roas_map = northbeam_connector.fetch_campaign_roas(
        lookback_days=config.DECISION_LOOKBACK_DAYS
    )

    # Fetch recent platform analytics as fallback
    end   = date.today() - timedelta(days=1)
    start = end - timedelta(days=config.DECISION_LOOKBACK_DAYS - 1)

    all_campaigns = db.get_active_campaigns()
    if not all_campaigns:
        log.info("No active campaigns to optimize.")
        return []

    all_campaign_ids = [
        c["pinterest_campaign_id"] or c["campaign_id"]
        for c in all_campaigns
        if c.get("pinterest_campaign_id") or c.get("campaign_id")
    ]

    platform_analytics: dict[str, dict] = {}
    try:
        rows = pinterest.get_campaign_analytics(all_campaign_ids, start.isoformat(), end.isoformat())
        for row in rows:
            platform_analytics[row["campaign_id"]] = row
    except Exception as exc:
        log.warning("Pinterest analytics fetch failed (will use Northbeam only): %s", exc)

    actions: list[dict] = []

    for camp in all_campaigns:
        campaign_id = camp["campaign_id"]
        pin_id      = camp.get("pinterest_campaign_id") or campaign_id

        # Gate: already changed today?
        if _already_changed_today(campaign_id):
            log.debug("[%s/%s] Already changed today — skip.", camp["brand"], camp["campaign_type"])
            continue

        # Gate: minimum spend
        spend = platform_analytics.get(pin_id, {}).get("spend", 0)
        if spend < config.MIN_SPEND_FOR_DECISION:
            log.debug("[%s/%s] Insufficient spend $%.0f — skip.", camp["brand"], camp["campaign_type"], spend)
            continue

        # Gate: minimum runtime (72h = 3 days)
        if _days_since_launch(camp.get("launch_date")) < config.MIN_RUNTIME_DAYS:
            log.debug("[%s/%s] Too new — skip.", camp["brand"], camp["campaign_type"])
            continue

        nb_roas       = nb_roas_map.get(pin_id, {}).get("roas", 0)
        platform_roas = platform_analytics.get(pin_id, {}).get("roas", 0)

        action = _decide(camp, nb_roas, platform_roas)
        actions.append(action)

        if config.SHADOW_MODE:
            log.info("[SHADOW] %s %s → %s (would budget $%.0f→$%.0f)",
                     camp["brand"], camp["campaign_type"], action["action"],
                     action["prior_budget_usd"], action["new_budget_usd"])
            db.log_decision(action)
            continue

        # Execute the decision
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
            "[%s/%s] %s — ROAS=%.2fx, budget $%.0f→$%.0f",
            camp["brand"], camp["campaign_type"], action["action"].upper(),
            action["roas_basis"], action["prior_budget_usd"], action["new_budget_usd"],
        )

    return actions
