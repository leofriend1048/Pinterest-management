"""
Campaign launcher module.

Handles:
  1. Weekly discovery campaign creation (Mondays)
  2. Monthly Performance+ challenger campaign (first Monday of the month)
  3. Scale campaign bootstrap (idempotent — runs every daily cycle)

Naming convention
-----------------
  Campaign : {CODE} | {FUNNEL} | {TYPE}    | {AUDIENCE}          | {PERIOD}
             MTB    | TOF      | Test       | W11-2026             (discovery)
             MTB    | TOF      | Scale      | Actalike            | Q1-2026
             MTB    | TOF      | Scale      | Perf+               | Q1-2026
             MTB    | TOF      | Scale      | Broad               | Q1-2026
             MTB    | TOF      | Scale      | Keyword             | Q1-2026
             MTB    | TOF      | Challenger | Perf+               | Mar-2026

  Ad set  : {CODE} | {TARGETING_DETAIL}              | {GEO} | {BID}
             MTB    | Actalike-Purchasers-180d        | US    | Auto
             MTB    | Perf+-Automated                 | US    | Auto
             MTB    | Interest-Beauty+KW-Broad        | US    | Auto
             MTB    | KW-Dermaplaning+SkinCare        | US    | Auto
             MTB    | Actalike-Purchasers-1-3pct      | US    | Auto

  Ad      : (same name as the originating Meta creative — no renaming)

Targeting specs per campaign type
----------------------------------
  discovery        — Actalike 1–3%, purchasers seed, interest expansion ON
  scale_actalike   — Actalike 0–2%, purchasers seed (180-day)
  scale_pplus      — Pinterest Performance+ automated
  scale_broad      — Interest + broad keywords
  scale_keyword    — Exact/phrase keywords only
"""

import logging
from datetime import date, datetime

import config
from connectors import pinterest_connector as pinterest
from intelligence.creative_scorer import fetch_and_score_meta_creatives
from notifications import slack
from state import db

log = logging.getLogger(__name__)


# ── Naming helpers ────────────────────────────────────────────────────────────

def _brand_code(brand: str) -> str:
    return config.BRAND_CONFIG.get(brand, {}).get("code", brand.upper()[:3])


def _geo_label(brand: str) -> str:
    geos = config.BRAND_CONFIG.get(brand, {}).get("geo", ["US"])
    return "+".join(geos)


def _quarter_label(d: date | None = None) -> str:
    d = d or date.today()
    q = (d.month - 1) // 3 + 1
    return f"Q{q}-{d.year}"


def _week_label(d: date | None = None) -> str:
    d = d or date.today()
    return f"W{d.strftime('%W')}-{d.year}"


def _month_label(d: date | None = None) -> str:
    d = d or date.today()
    return d.strftime("%b-%Y")


def _campaign_name(brand: str, camp_type: str, today: date | None = None) -> str:
    """Return the standardised campaign name for the given type."""
    today = today or date.today()
    code  = _brand_code(brand)
    _TYPE_MAP = {
        "discovery":      ("TOF", "Test",       None,       _week_label(today)),
        "scale_actalike": ("TOF", "Scale",      "Actalike", _quarter_label(today)),
        "scale_pplus":    ("TOF", "Scale",      "Perf+",    _quarter_label(today)),
        "scale_broad":    ("TOF", "Scale",      "Broad",    _quarter_label(today)),
        "scale_keyword":  ("TOF", "Scale",      "Keyword",  _quarter_label(today)),
        "challenger":     ("TOF", "Challenger", "Perf+",    _month_label(today)),
    }
    funnel, ctype_label, audience, period = _TYPE_MAP.get(
        camp_type, ("TOF", camp_type.title(), None, _quarter_label(today))
    )
    if audience:
        return f"{code} | {funnel} | {ctype_label} | {audience} | {period}"
    return f"{code} | {funnel} | {ctype_label} | {period}"


def _adset_name(brand: str, targeting_detail: str) -> str:
    """Return the standardised ad set name."""
    code = _brand_code(brand)
    geo  = _geo_label(brand)
    return f"{code} | {targeting_detail} | {geo} | Auto"


# ── Targeting specs ───────────────────────────────────────────────────────────

def _actalike_audiences(brand: str) -> list[str]:
    """Return configured seed audience IDs, or empty list."""
    aud_id = config.BRAND_AUDIENCE_IDS.get(brand, "")
    return [aud_id] if aud_id else []


def _base_targeting(brand: str) -> dict:
    """Common targeting applied to every ad group: geo, gender, age."""
    return {
        "geo":         config.BRAND_CONFIG.get(brand, {}).get("geo", ["US"]),
        "genders":     config.TARGET_GENDER,
        "age_bucket":  config.TARGET_AGE_BUCKETS,
    }


def _discovery_targeting(brand: str) -> dict:
    spec = _base_targeting(brand)
    spec.update({
        "audience_types":          ["ACTALIKE"],
        "actalike_type":           "ACTALIKE",
        "actalike_sizes":          list(config.ACTALIKE_DISC_SIZE_RANGE),
        "auto_interest_expansion": True,
    })
    audiences = _actalike_audiences(brand)
    if audiences:
        spec["audiences"] = audiences
    return spec


def _actalike_targeting(brand: str) -> dict:
    spec = _base_targeting(brand)
    spec.update({
        "audience_types": ["ACTALIKE"],
        "actalike_sizes": list(config.ACTALIKE_SCALE_SIZE_RANGE),
    })
    audiences = _actalike_audiences(brand)
    if audiences:
        spec["audiences"] = audiences
    return spec


def _pplus_targeting(brand: str) -> dict:
    """Performance+ automated targeting — still apply gender/age."""
    spec = _base_targeting(brand)
    spec["audience_types"] = ["AUTOPILOT"]
    return spec


def _broad_targeting(brand: str) -> dict:
    spec = _base_targeting(brand)
    spec.update({
        "audience_types": ["KEYWORD"],
        "keywords": [{"match_type": "BROAD", "value": kw}
                     for kw in config.KEYWORD_SEEDS.get(brand, [])],
    })
    interest_ids = config.INTEREST_SEEDS.get(brand, [])
    if interest_ids:
        spec["audience_types"] = ["INTEREST", "KEYWORD"]
        spec["interests"] = interest_ids
    return spec


def _keyword_targeting(brand: str) -> dict:
    spec = _base_targeting(brand)
    spec.update({
        "audience_types": ["KEYWORD"],
        "keywords": [{"match_type": "EXACT", "value": kw}
                     for kw in config.KEYWORD_SEEDS.get(brand, [])],
    })
    return spec


TARGETING_FACTORY = {
    "discovery":      _discovery_targeting,
    "scale_actalike": _actalike_targeting,
    "scale_pplus":    _pplus_targeting,
    "scale_broad":    _broad_targeting,
    "scale_keyword":  _keyword_targeting,
}

# Human-readable targeting detail used in ad set names
_ADSET_DETAIL = {
    "discovery":      f"Actalike-Purchasers-{config.ACTALIKE_DISC_SIZE_RANGE[0]*100:.0f}-{config.ACTALIKE_DISC_SIZE_RANGE[1]*100:.0f}pct",
    "scale_actalike": f"Actalike-Purchasers-{config.ACTALIKE_SEED_DAYS}d",
    "scale_pplus":    "Perf+-Automated",
    "scale_broad":    "Interest-Beauty+KW-Broad",
    "scale_keyword":  "KW-Dermaplaning+SkinCare",
    "challenger":     "Perf+-Automated",
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

    Naming: MTB | TOF | Test | W11-2026
    Ad set: MTB | Actalike-Purchasers-1-3pct | US | Auto
    Ads:    same name as the originating Meta creative
    """
    log.info("[%s] Launching weekly discovery campaign.", brand)

    creatives = fetch_and_score_meta_creatives(brand)
    if not creatives:
        log.info("[%s] No qualifying creatives — skipping discovery launch.", brand)
        return None

    today         = date.today()
    camp_name     = _campaign_name(brand, "discovery", today)
    adset_detail  = _ADSET_DETAIL["discovery"]
    adset_nm      = _adset_name(brand, adset_detail)
    budget        = config.CAMPAIGN_BUDGETS["discovery"]
    landing_url   = config.BRAND_LANDING_URLS.get(brand, "https://example.com")

    if config.SHADOW_MODE:
        log.info("[SHADOW] Would create discovery campaign: %s", camp_name)
        return None

    campaign_id = pinterest.create_campaign(
        name=camp_name,
        daily_budget_usd=budget,
    )

    targeting    = _discovery_targeting(brand)
    ad_group_id  = pinterest.create_ad_group(
        campaign_id=campaign_id,
        name=adset_nm,
        targeting_spec=targeting,
    )

    board_id = config.BRAND_BOARD_IDS.get(brand, "")
    for c in creatives:
        media_id = _upload_creative(c)
        if not media_id:
            continue

        # Step 4 (rep's flow): create an organic Pin from the processed media,
        # then promote that Pin as an ad (step 5).
        pin_id = None
        if board_id:
            try:
                pin_id = pinterest.create_pin(
                    media_id=media_id,
                    board_id=board_id,
                    link=landing_url,
                    title=config.AD_TITLE.get(brand, ""),
                    description=config.AD_DESCRIPTION.get(brand, ""),
                )
            except Exception as exc:
                log.warning("Pin creation failed for %s — falling back to media_id: %s",
                            c["meta_creative_id"], exc)
        else:
            log.warning("[%s] MTB_PINTEREST_BOARD_ID not set — promoting via media_id (no organic pin)", brand)

        # Ad name = Meta creative name (unchanged)
        ad_name = c.get("name") or c["meta_creative_id"]
        ad_id   = pinterest.create_ad(
            ad_group_id=ad_group_id,
            name=ad_name,
            pin_id=pin_id,
            media_id=media_id if not pin_id else None,
            link=landing_url,
            call_to_action=config.AD_CTA,
            title=config.AD_TITLE.get(brand, ""),
            description=config.AD_DESCRIPTION.get(brand, ""),
        )
        db.set_creative_pin_id(c["meta_creative_id"], "discovery", ad_id)

    db.upsert_campaign(
        {
            "campaign_id":           campaign_id,
            "brand":                 brand,
            "campaign_type":         "discovery",
            "pinterest_campaign_id": campaign_id,
            "status":                "active",
            "budget_usd":            budget,
            "launch_date":           today.isoformat(),
            "is_challenger":         0,
        }
    )

    log.info("[%s] Discovery campaign launched: %s (%s)", brand, camp_name, campaign_id)
    return campaign_id


# ── Monthly Performance+ challenger ──────────────────────────────────────────

def launch_monthly_challenger(brand: str) -> str | None:
    """
    On the first Monday of each month, launch a Performance+ challenger campaign.
    Returns the Pinterest campaign ID, or None.

    Naming: MTB | TOF | Challenger | Perf+ | Mar-2026
    Ad set: MTB | Perf+-Automated | US | Auto
    """
    today = date.today()
    if today.weekday() != 0 or today.day > 7:
        return None  # Only first Monday of the month

    log.info("[%s] Launching monthly Performance+ challenger.", brand)

    camp_name    = _campaign_name(brand, "challenger", today)
    adset_nm     = _adset_name(brand, _ADSET_DETAIL["challenger"])
    budget       = config.CAMPAIGN_BUDGETS["challenger"]

    if config.SHADOW_MODE:
        log.info("[SHADOW] Would create challenger: %s", camp_name)
        return None

    campaign_id = pinterest.create_campaign(
        name=camp_name,
        daily_budget_usd=budget,
        objective="CATALOG_SALES",
    )

    targeting   = _pplus_targeting(brand)
    ad_group_id = pinterest.create_ad_group(
        campaign_id=campaign_id,
        name=adset_nm,
        targeting_spec=targeting,
    )

    db.upsert_campaign(
        {
            "campaign_id":           campaign_id,
            "brand":                 brand,
            "campaign_type":         "scale_pplus",
            "pinterest_campaign_id": campaign_id,
            "status":                "active",
            "budget_usd":            budget,
            "launch_date":           today.isoformat(),
            "is_challenger":         1,
        }
    )

    log.info("[%s] Challenger campaign launched: %s (%s)", brand, camp_name, campaign_id)
    return campaign_id


# ── Scale campaign bootstrap (idempotent, runs every cycle) ───────────────────

def ensure_scale_campaigns_exist(brand: str) -> dict[str, str]:
    """
    Ensure all four scale campaigns exist for the brand.
    Creates any that are missing.  Returns {campaign_type: campaign_id}.

    Naming examples (MTB):
      MTB | TOF | Scale | Actalike | Q1-2026
      MTB | TOF | Scale | Perf+    | Q1-2026
      MTB | TOF | Scale | Broad    | Q1-2026
      MTB | TOF | Scale | Keyword  | Q1-2026

    Ad set examples:
      MTB | Actalike-Purchasers-180d       | US | Auto
      MTB | Perf+-Automated                | US | Auto
      MTB | Interest-Beauty+KW-Broad       | US | Auto
      MTB | KW-Dermaplaning+SkinCare       | US | Auto
    """
    existing = {
        c["campaign_type"]: c["campaign_id"]
        for c in db.get_active_campaigns(brand)
        if c["campaign_type"] in config.SCALE_CAMPAIGN_TYPES
        and not c.get("is_challenger")
    }

    today = date.today()

    for ctype in config.SCALE_CAMPAIGN_TYPES:
        if ctype in existing:
            continue

        camp_name   = _campaign_name(brand, ctype, today)
        adset_nm    = _adset_name(brand, _ADSET_DETAIL[ctype])
        budget      = config.CAMPAIGN_BUDGETS[ctype]
        landing_url = config.BRAND_LANDING_URLS.get(brand, "https://example.com")

        if config.SHADOW_MODE:
            log.info("[SHADOW] Would create scale campaign: %s", camp_name)
            existing[ctype] = f"shadow_{ctype}"
            continue

        targeting_fn = TARGETING_FACTORY[ctype]
        targeting    = targeting_fn(brand)

        campaign_id  = pinterest.create_campaign(camp_name, budget)
        ad_group_id  = pinterest.create_ad_group(campaign_id, adset_nm, targeting)

        db.upsert_campaign(
            {
                "campaign_id":           campaign_id,
                "brand":                 brand,
                "campaign_type":         ctype,
                "pinterest_campaign_id": campaign_id,
                "status":                "active",
                "budget_usd":            budget,
                "launch_date":           today.isoformat(),
                "is_challenger":         0,
            }
        )
        existing[ctype] = campaign_id
        log.info("[%s] Created scale campaign %s: %s (%s)", brand, ctype, camp_name, campaign_id)

    return existing
