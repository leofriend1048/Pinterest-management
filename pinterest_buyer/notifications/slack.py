"""
Slack notification layer for PAMBS.

All alerts are posted to a single channel defined by SLACK_CHANNEL in config.
Real-time alerts fire immediately; the morning digest is batched and posted at 7 AM.
"""

import logging
from datetime import datetime, timezone

from slack_sdk import WebClient
from slack_sdk.errors import SlackApiError

import config

log = logging.getLogger(__name__)

_client: WebClient | None = None


def _get_client() -> WebClient:
    global _client
    if _client is None:
        _client = WebClient(token=config.SLACK_BOT_TOKEN)
    return _client


def _post(blocks: list | None = None, text: str = "") -> None:
    try:
        _get_client().chat_postMessage(
            channel=config.SLACK_CHANNEL,
            text=text,
            blocks=blocks or [],
        )
    except SlackApiError as exc:
        log.error("Slack post failed: %s", exc.response["error"])


def _section(text: str) -> dict:
    return {"type": "section", "text": {"type": "mrkdwn", "text": text}}


def _divider() -> dict:
    return {"type": "divider"}


def _header(text: str) -> dict:
    return {"type": "header", "text": {"type": "plain_text", "text": text}}


# ── Real-time alerts ──────────────────────────────────────────────────────────

def alert_graduation(
    creative_name: str,
    brand: str,
    spend_usd: float,
    roas: float,
    scale_campaigns: list[str],
) -> None:
    brands_label = brand.replace("_", " ").title()
    campaigns_str = ", ".join(scale_campaigns)
    blocks = [
        _header("🎓 Creative Graduated to Scale"),
        _section(
            f"*Brand:* {brands_label}\n"
            f"*Creative:* {creative_name}\n"
            f"*Spend at graduation:* ${spend_usd:,.2f}\n"
            f"*ROAS achieved:* {roas:.2f}x\n"
            f"*Scale campaigns entered:* {campaigns_str}"
        ),
    ]
    _post(blocks, text=f"[PAMBS] {creative_name} graduated to scale ({brands_label})")
    log.info("Slack: graduation alert sent for %s", creative_name)


def alert_p0_anomaly(
    brand: str,
    what_changed: str,
    magnitude: str,
    root_cause: str,
    recommended_action: str,
) -> None:
    blocks = [
        _header("🚨 P0 Anomaly Detected"),
        _section(
            f"*Brand:* {brand.replace('_', ' ').title()}\n"
            f"*What changed:* {what_changed}\n"
            f"*Magnitude:* {magnitude}\n"
            f"*Root cause hypothesis:* {root_cause}\n"
            f"*Recommended action:* {recommended_action}"
        ),
    ]
    _post(blocks, text=f"[PAMBS] P0 Anomaly — {brand} — {what_changed}")
    log.warning("Slack: P0 anomaly sent for %s: %s", brand, what_changed)


def alert_budget_change(
    campaign_id: str,
    campaign_type: str,
    brand: str,
    old_budget: float,
    new_budget: float,
    roas_basis: float,
    rationale: str,
) -> None:
    direction = "↑" if new_budget > old_budget else "↓"
    blocks = [
        _header(f"💰 Budget Change {direction}"),
        _section(
            f"*Brand:* {brand.replace('_', ' ').title()}\n"
            f"*Campaign:* {campaign_type} (`{campaign_id}`)\n"
            f"*Budget:* ${old_budget:,.0f}/day → ${new_budget:,.0f}/day\n"
            f"*ROAS basis:* {roas_basis:.2f}x\n"
            f"*Rationale:* {rationale}"
        ),
    ]
    _post(blocks, text=f"[PAMBS] Budget {direction} {brand} {campaign_type}")
    log.info("Slack: budget change alert sent for %s %s", brand, campaign_type)


def alert_campaign_paused(
    campaign_id: str,
    campaign_type: str,
    brand: str,
    roas: float,
    reason: str,
) -> None:
    blocks = [
        _header("⏸️ Campaign Paused"),
        _section(
            f"*Brand:* {brand.replace('_', ' ').title()}\n"
            f"*Campaign:* {campaign_type} (`{campaign_id}`)\n"
            f"*ROAS at pause:* {roas:.2f}x\n"
            f"*Reason:* {reason}"
        ),
    ]
    _post(blocks, text=f"[PAMBS] Campaign paused — {brand} {campaign_type}")
    log.info("Slack: campaign pause alert sent for %s %s", brand, campaign_type)


def alert_ad_paused(
    ad_id: str,
    ad_name: str,
    campaign_type: str,
    brand: str,
    spend_usd: float,
    roas: float,
    reason: str,
    roas_1d: float | None = None,
    roas_3d: float | None = None,
    roas_7d: float | None = None,
) -> None:
    roas_line = f"*ROAS (7d):* {roas:.2f}x"
    if roas_1d is not None and roas_3d is not None and roas_7d is not None:
        roas_line = (
            f"*ROAS windows:* 1d={roas_1d:.2f}x | 3d={roas_3d:.2f}x | 7d={roas_7d:.2f}x"
        )
    blocks = [
        _header("⏸️ Ad Paused"),
        _section(
            f"*Brand:* {brand.replace('_', ' ').title()}\n"
            f"*Ad:* {ad_name} (`{ad_id}`)\n"
            f"*Campaign:* {campaign_type}\n"
            f"*Spend (7d):* ${spend_usd:,.2f}\n"
            f"{roas_line}\n"
            f"*Reason:* {reason}"
        ),
    ]
    _post(blocks, text=f"[PAMBS] Ad paused — {ad_name} ({brand})")
    log.info("Slack: ad pause alert sent for %s in %s/%s", ad_name, brand, campaign_type)


def alert_frequency_rotation(
    creative_name: str,
    brand: str,
    frequency: float,
    next_creative: str,
) -> None:
    blocks = [
        _header("🔄 Creative Rotated (Frequency Cap)"),
        _section(
            f"*Brand:* {brand.replace('_', ' ').title()}\n"
            f"*Creative rotated out:* {creative_name}\n"
            f"*Frequency hit:* {frequency:.1f}\n"
            f"*Next in queue:* {next_creative}"
        ),
    ]
    _post(blocks, text=f"[PAMBS] Frequency rotation — {creative_name}")


def post_morning_digest(digest_text: str) -> None:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    blocks = [
        _header(f"📊 Pinterest Morning Digest — {now}"),
        _divider(),
        _section(digest_text),
    ]
    _post(blocks, text=f"[PAMBS] Morning digest {now}")
    log.info("Slack: morning digest posted")


def post_weekly_opportunity_update(report_text: str) -> None:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    blocks = [
        _header(f"🔍 Weekly Opportunity Update — {now}"),
        _divider(),
        _section(report_text),
    ]
    _post(blocks, text=f"[PAMBS] Weekly opportunity update {now}")
    log.info("Slack: weekly opportunity update posted")


def post_shadow_summary(actions: list[dict]) -> None:
    """Post a shadow-mode summary of what *would* have been done."""
    lines = ["*[SHADOW MODE] Actions that would have fired:*\n"]
    for a in actions:
        lines.append(
            f"• `{a.get('action')}` — {a.get('brand')} / {a.get('campaign_type')} "
            f"— {a.get('rationale', '')}"
        )
    _post([_section("\n".join(lines))], text="[PAMBS SHADOW] Simulated actions")
    log.info("Slack: shadow summary posted (%d actions)", len(actions))
