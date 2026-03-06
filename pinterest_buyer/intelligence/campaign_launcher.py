"""
Campaign launcher module.

Handles:
  1. Weekly discovery campaign creation (Mondays)
  2. Monthly Performance+ challenger campaign (first Monday of the month)

Targeting specs per campaign type
----------------------------------
  discovery        — Actalike 1–3%, Pinterest interest expansion ON
  scale_actalike   — Actalike 0–2%, manually defined
  scale_pplus      — Pinterest automated (Performance+)
  scale_broad      — Interest + keyword targeting combined
  scale_keyword    — Search keyword targeting only
"""

import logging
from datetime import date, datetime

import config
from connectors import pinterest_connector as pinterest
from intelligence.creative_scorer import fetch_and_score_meta_creatives
from notifications import slack
from state import db

log = logging.getLogger(__name__)


# ── Targeting specs ───────────────────────────────────────────────────────────

def _discovery_targeting(brand: str) -> dict:
    return {
        "audience_types": ["ACTALIKE"],
        "actalike_sources": [],        # populated from existing customer lists
        "actalike_type": "ACTALIKE",
        "actalike_sizes": [0.01, 0.03],  # 1–3%
        "auto_interest_expansion": True,
    }


def _actalike_targeting() -> dict:
    return {
        "audience_types": ["ACTALIKE"],
        "actalike_sizes": [0.00, 0.02],  # 0–2%
    }


def _pplus_targeting() -> dict:
    """Performance+ uses Pinterest automated targeting."""
    return {"audience_types": ["AUTOPILOT"]}


def _broad_targeting(brand: str) -> dict:
    return {
        "audience_types": ["INTEREST", "KEYWORD"],
        "interests": [],   # populated from Pinterest interest taxonomy
        "keywords": [{"match_type": "BROAD", "value": kw}
                     for kw in config.KEYWORD_SEEDS.get(brand, [])],
    }


def _keyword_targeting(brand: str) -> dict:
    return {
        "audience_types": ["KEYWORD"],
        "keywords": [{"match_type": "EXACT", "value": kw}
                     for kw in config.KEYWORD_SEEDS.get(brand, [])],
    }


TARGETING_FACTORY = {
    "discovery":      _discovery_targeting,
    "scale_actalike": lambda brand: _actalike_targeting(),
    "scale_pplus":    lambda brand: _pplus_targeting(),
    "scale_broad":    _broad_targeting,
    "scale_keyword":  _keyword_targeting,
}


# ── Media upload helper ───────────────────────────────────────────────────────

def _upload_creative(creative: dict) -> str | None:
    """Upload a Meta asset to Pinterest and return the Pinterest media_id."""
    local_path = creative.get("asset_local_path")
    if not local_path:
        log.warning("No local asset for creative %s — skip upload", creative["meta_creative_id"])
        return None
    try:
        if creative.get("creative_format") == "video":
            return pinterest.upload_video(local_path)
        return pinterest.upload_image(local_path)
    except Exception as exc:
        log.error("Asset upload failed for %s: %s", creative["meta_creative_id"], exc)
        return None


# ── Weekly discovery campaign ─────────────────────────────────────────────────

def launch_weekly_discovery(brand: str) -> str | None:
    """
    Create a new weekly discovery campaign for the given brand.
    Returns the new Pinterest campaign ID, or None if no creatives qualify.
    """
    log.info("[%s] Launching weekly discovery campaign.", brand)

    creatives = fetch_and_score_meta_creatives(brand)
    if not creatives:
        log.info("[%s] No qualifying creatives — skipping discovery launch.", brand)
        return None

    week_label = date.today().strftime("%Y-W%W")
    campaign_name = f"{brand} — Discovery {week_label}"

    if config.SHADOW_MODE:
        log.info("[SHADOW] Would create campaign: %s", campaign_name)
        return None

    # Create the Pinterest campaign
    campaign_id = pinterest.create_campaign(
        name=campaign_name,
        daily_budget_usd=config.DISCOVERY_CAMPAIGN_BUDGET_USD,
    )

    # Create one ad group with discovery targeting
    targeting = _discovery_targeting(brand)
    ad_group_id = pinterest.create_ad_group(
        campaign_id=campaign_id,
        name=f"{brand} — Discovery AdGroup {week_label}",
        targeting_spec=targeting,
    )

    # Upload each creative and create ads
    for c in creatives:
        media_id = _upload_creative(c)
        if not media_id:
            continue
        ad_id = pinterest.create_ad(
            ad_group_id=ad_group_id,
            name=c.get("name", c["meta_creative_id"]),
            media_id=media_id,
        )
        db.set_creative_pin_id(c["meta_creative_id"], "discovery", ad_id)

    # Persist to campaign_log
    db.upsert_campaign(
        {
            "campaign_id":           campaign_id,
            "brand":                 brand,
            "campaign_type":         "discovery",
            "pinterest_campaign_id": campaign_id,
            "status":                "active",
            "budget_usd":            config.DISCOVERY_CAMPAIGN_BUDGET_USD,
            "launch_date":           date.today().isoformat(),
            "is_challenger":         0,
        }
    )

    log.info("[%s] Discovery campaign launched: %s", brand, campaign_id)
    return campaign_id


# ── Monthly Performance+ challenger ──────────────────────────────────────────

def launch_monthly_challenger(brand: str) -> str | None:
    """
    On the first Monday of each month, launch a Performance+ challenger campaign
    at $300/day using current top creatives.
    Returns the Pinterest campaign ID, or None.
    """
    today = date.today()
    if today.weekday() != 0 or today.day > 7:
        return None  # Only runs on the first Monday of the month

    log.info("[%s] Launching monthly Performance+ challenger.", brand)

    # Reuse already-scored creatives from the DB (last 14 days)
    from connectors import northbeam_connector
    nb_data = northbeam_connector.fetch_campaign_roas(lookback_days=14)

    month_label = today.strftime("%Y-%m")
    campaign_name = f"{brand} — P+ Challenger {month_label}"

    if config.SHADOW_MODE:
        log.info("[SHADOW] Would create challenger: %s", campaign_name)
        return None

    campaign_id = pinterest.create_campaign(
        name=campaign_name,
        daily_budget_usd=config.CHALLENGER_BUDGET_USD,
        objective="CATALOG_SALES",
    )

    targeting = _pplus_targeting()
    ad_group_id = pinterest.create_ad_group(
        campaign_id=campaign_id,
        name=f"{brand} — P+ Challenger AdGroup {month_label}",
        targeting_spec=targeting,
    )

    db.upsert_campaign(
        {
            "campaign_id":           campaign_id,
            "brand":                 brand,
            "campaign_type":         "scale_pplus",
            "pinterest_campaign_id": campaign_id,
            "status":                "active",
            "budget_usd":            config.CHALLENGER_BUDGET_USD,
            "launch_date":           today.isoformat(),
            "is_challenger":         1,
        }
    )

    log.info("[%s] Challenger campaign launched: %s", brand, campaign_id)
    return campaign_id


# ── Scale campaign bootstrap (run once per brand) ─────────────────────────────

def ensure_scale_campaigns_exist(brand: str) -> dict[str, str]:
    """
    Ensure all four scale campaigns exist for the brand.
    Creates any that are missing.  Returns {campaign_type: campaign_id}.
    """
    existing = {
        c["campaign_type"]: c["campaign_id"]
        for c in db.get_active_campaigns(brand)
        if c["campaign_type"] in config.SCALE_CAMPAIGN_TYPES
        and not c.get("is_challenger")
    }

    for ctype in config.SCALE_CAMPAIGN_TYPES:
        if ctype in existing:
            continue

        name = f"{brand} — Scale: {ctype.replace('scale_', '').title()}"
        if config.SHADOW_MODE:
            log.info("[SHADOW] Would create scale campaign: %s", name)
            existing[ctype] = f"shadow_{ctype}"
            continue

        targeting_fn = TARGETING_FACTORY[ctype]
        targeting    = targeting_fn(brand)

        campaign_id  = pinterest.create_campaign(name, config.SCALE_CAMPAIGN_BUDGET_USD)
        ad_group_id  = pinterest.create_ad_group(campaign_id, f"{name} AdGroup", targeting)

        db.upsert_campaign(
            {
                "campaign_id":           campaign_id,
                "brand":                 brand,
                "campaign_type":         ctype,
                "pinterest_campaign_id": campaign_id,
                "status":                "active",
                "budget_usd":            config.SCALE_CAMPAIGN_BUDGET_USD,
                "launch_date":           date.today().isoformat(),
                "is_challenger":         0,
            }
        )
        existing[ctype] = campaign_id
        log.info("[%s] Created scale campaign %s: %s", brand, ctype, campaign_id)

    return existing
