"""
Scale graduation engine.

Every morning, evaluate every active ad in every discovery campaign.
When a creative clears $300 spend AND ROAS ≥ 0.9× target ROAS,
add it simultaneously to all four scale campaigns for that brand.

The creative stays active in the discovery campaign after graduation.
Graduation fires a real-time Slack alert.
"""

import logging
from datetime import date, timedelta

import config
from connectors import pinterest_connector as pinterest
from notifications import slack
from state import db

log = logging.getLogger(__name__)


def _get_discovery_ad_performance(campaign_id: str, lookback_days: int = 14) -> list[dict]:
    """
    Pull ad-level analytics for a discovery campaign over the lookback window.
    """
    end   = date.today() - timedelta(days=1)
    start = end - timedelta(days=lookback_days - 1)

    # Pinterest ad analytics works at ad_group level; we pull all ads in campaign
    ads = pinterest.list_ads_in_campaign(campaign_id)
    if not ads:
        return []

    ad_ids = [a["id"] for a in ads if a.get("status") == "ACTIVE"]
    if not ad_ids:
        return []

    analytics = pinterest.get_ad_analytics(
        ad_ids,
        start.isoformat(),
        end.isoformat(),
    )

    # Merge name from ads list
    name_map = {a["id"]: a.get("name", a["id"]) for a in ads}
    for row in analytics:
        row["ad_name"] = name_map.get(row["ad_id"], row["ad_id"])

    return analytics


def _add_creative_to_scale_campaigns(
    brand: str,
    meta_creative_id: str,
    ad_name: str,
    scale_campaign_map: dict[str, str],   # {campaign_type: campaign_id}
    ad_group_map: dict[str, str],         # {campaign_id: ad_group_id}
    local_asset_path: str | None,
    creative_format: str,
) -> list[str]:
    """
    Add a creative as a new ad into all four scale campaigns.
    Returns list of campaign IDs successfully entered.
    """
    from connectors import pinterest_connector as pinterest

    # Upload asset once and create one organic Pin for all four scale campaigns.
    pin_id   = None
    media_id = None
    if local_asset_path and not config.SHADOW_MODE:
        try:
            if creative_format == "video":
                media_id = pinterest.upload_video(local_asset_path)
            else:
                media_id = pinterest.upload_image(local_asset_path)

            board_id = config.BRAND_BOARD_IDS.get(brand, "")
            if board_id and media_id:
                try:
                    pin_id = pinterest.create_pin(
                        media_id=media_id,
                        board_id=board_id,
                    )
                except Exception as exc:
                    log.warning("Pin creation failed for %s — will promote via media_id: %s",
                                ad_name, exc)
            elif not board_id:
                log.warning("[%s] BOARD_ID not set — promoting graduated creative via media_id", brand)
        except Exception as exc:
            log.error("Asset upload failed for %s: %s", ad_name, exc)

    entered: list[str] = []
    for ctype, campaign_id in scale_campaign_map.items():
        ad_group_id = ad_group_map.get(campaign_id)
        if not ad_group_id:
            log.warning("No ad group found for scale campaign %s — skipping", campaign_id)
            continue

        try:
            if config.SHADOW_MODE:
                log.info("[SHADOW] Would add %s to %s (%s)", ad_name, campaign_id, ctype)
                entered.append(campaign_id)
                continue

            ad_id = pinterest.create_ad(
                ad_group_id=ad_group_id,
                name=ad_name,
                pin_id=pin_id,
                media_id=media_id if not pin_id else None,
            )
            db.set_creative_pin_id(meta_creative_id, ctype, ad_id)
            entered.append(campaign_id)
            log.info("Added %s to scale campaign %s (%s)", ad_name, campaign_id, ctype)
        except Exception as exc:
            log.error("Failed to add %s to %s: %s", ad_name, campaign_id, exc)

    return entered


def _get_ad_group_map(brand: str) -> dict[str, str]:
    """Build {campaign_id: ad_group_id} by querying Pinterest API."""
    campaigns = db.get_active_campaigns(brand)
    ad_group_map: dict[str, str] = {}
    for camp in campaigns:
        cid = camp["pinterest_campaign_id"] or camp["campaign_id"]
        try:
            resp = pinterest._req(
                "GET",
                f"/ad_accounts/{config.PINTEREST_AD_ACCOUNT_ID}/ad_groups",
                params={"campaign_ids": cid, "page_size": 10},
            )
            items = resp.get("items", [])
            if items:
                ad_group_map[cid] = items[0]["id"]
        except Exception as exc:
            log.warning("Could not fetch ad groups for campaign %s: %s", cid, exc)
    return ad_group_map


def run_graduation_check(brand: str) -> list[dict]:
    """
    Main entry point: evaluate all discovery ads for this brand,
    graduate qualifying creatives to all four scale campaigns.

    Returns list of graduation event dicts for the morning digest.
    """
    target_roas = config.TARGET_ROAS[brand]
    threshold   = target_roas * config.GRADUATION_MIN_ROAS_MULTIPLIER

    # Fetch all discovery campaigns for this brand
    discovery_campaigns = [
        c for c in db.get_active_campaigns(brand)
        if c["campaign_type"] == "discovery"
    ]
    if not discovery_campaigns:
        log.info("[%s] No active discovery campaigns — skip graduation check.", brand)
        return []

    # Fetch scale campaign map {type: campaign_id}
    scale_campaigns = {
        c["campaign_type"]: c["pinterest_campaign_id"] or c["campaign_id"]
        for c in db.get_active_campaigns(brand)
        if c["campaign_type"] in config.SCALE_CAMPAIGN_TYPES
        and not c.get("is_challenger")
    }
    if len(scale_campaigns) < len(config.SCALE_CAMPAIGN_TYPES):
        log.warning("[%s] Missing scale campaigns — graduation may be incomplete.", brand)

    already_graduated = db.get_graduated_creative_ids(brand)
    ad_group_map      = _get_ad_group_map(brand)

    events: list[dict] = []

    for camp in discovery_campaigns:
        campaign_id = camp["pinterest_campaign_id"] or camp["campaign_id"]
        analytics   = _get_discovery_ad_performance(campaign_id)

        for row in analytics:
            spend = row.get("spend", 0)
            roas  = row.get("roas", 0)
            ad_id = row.get("ad_id")
            ad_name = row.get("ad_name", ad_id)

            if spend < config.GRADUATION_MIN_SPEND_USD or roas < threshold:
                continue
            if ad_id in already_graduated:
                continue

            # Look up creative in mirror to get Meta creative ID + asset
            with db.get_db() as conn:
                creative_row = conn.execute(
                    """SELECT meta_creative_id, asset_local_path, creative_format
                       FROM creative_mirror
                       WHERE json_extract(pinterest_pin_ids, '$.discovery') = ?""",
                    (ad_id,)
                ).fetchone()

            meta_id  = creative_row["meta_creative_id"] if creative_row else ad_id
            asset    = creative_row["asset_local_path"] if creative_row else None
            fmt      = creative_row["creative_format"] if creative_row else "image"

            entered = _add_creative_to_scale_campaigns(
                brand=brand,
                meta_creative_id=meta_id,
                ad_name=ad_name,
                scale_campaign_map=scale_campaigns,
                ad_group_map=ad_group_map,
                local_asset_path=asset,
                creative_format=fmt,
            )

            if not entered:
                continue

            # Log the graduation
            event = {
                "meta_creative_id":    meta_id,
                "brand":               brand,
                "spend_at_graduation": spend,
                "roas_at_graduation":  roas,
                "scale_campaigns":     entered,
            }
            db.log_graduation(event)
            db.mark_creative_graduated(meta_id)
            already_graduated.add(meta_id)
            events.append({**event, "ad_name": ad_name})

            # Real-time Slack alert
            campaign_type_labels = [
                t for t, cid in scale_campaigns.items() if cid in entered
            ]
            slack.alert_graduation(
                creative_name=ad_name,
                brand=brand,
                spend_usd=spend,
                roas=roas,
                scale_campaigns=campaign_type_labels,
            )
            log.info("[%s] Graduated %s (spend=$%.0f, ROAS=%.2f)", brand, ad_name, spend, roas)

    log.info("[%s] Graduation check complete — %d creatives graduated.", brand, len(events))
    return events
