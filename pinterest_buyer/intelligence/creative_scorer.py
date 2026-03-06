"""
Creative scoring module.

Score = (ROAS × 0.45) + (NCPA_inverse × 0.35) + (CTR × 0.10) + (Hook_Rate × 0.10)

NCPA_inverse is normalised: lower NCPA is better, so we invert it relative to
the maximum NCPA observed in the candidate set.  This keeps the weight meaningful
without requiring hard-coded NCPA baselines.
"""

import logging
from datetime import date, timedelta
from typing import Any

import config
from connectors.meta_connector import fetch_top_creatives, download_asset
from state import db

log = logging.getLogger(__name__)


def _ncpa_inverse(ncpa: float, max_ncpa: float) -> float:
    """Return a normalised 0-1 value where lower NCPA → higher score."""
    if max_ncpa <= 0 or ncpa <= 0:
        return 0.0
    return 1.0 - min(ncpa / max_ncpa, 1.0)


def score_creative(c: dict, max_ncpa: float) -> float:
    return (
        c["roas"]      * config.WEIGHT_ROAS
        + _ncpa_inverse(c["ncpa"], max_ncpa) * config.WEIGHT_NCPA
        + c["ctr"]     * config.WEIGHT_CTR
        + c["hook_rate"] * config.WEIGHT_HOOK_RATE
    )


def fetch_and_score_meta_creatives(brand: str) -> list[dict]:
    """
    Pull top Meta creatives for this brand, score them, upsert into
    creative_mirror, and return the ranked list ready for Pinterest launch.

    Returns list of creative dicts with 'composite_score' added.
    Excludes creatives already in creative_mirror (already mirrored).
    """
    already_mirrored = db.get_unmirrored_creative_ids(brand)
    already_graduated = db.get_graduated_creative_ids(brand)
    exclude = already_mirrored | already_graduated

    # We fetch more than top_n to account for exclusions
    raw = fetch_top_creatives(
        lookback_days=config.META_CREATIVE_LOOKBACK_DAYS,
        min_spend=config.META_CREATIVE_MIN_SPEND_USD,
        top_n=config.META_TOP_N_CREATIVES * 3,
    )

    candidates = [c for c in raw if c["meta_creative_id"] not in exclude]

    if not candidates:
        log.info("[%s] No new Meta creatives qualify for mirroring.", brand)
        return []

    max_ncpa = max((c["ncpa"] for c in candidates if c["ncpa"] > 0), default=1.0)

    today = date.today().isoformat()
    scored: list[dict] = []
    for c in candidates:
        c["composite_score"] = score_creative(c, max_ncpa)
        c["brand"]           = brand
        c["first_seen_date"] = today
        c["last_scored_date"] = today
        c["meta_spend_usd"]  = c.pop("spend")
        c["meta_roas"]       = c.pop("roas")
        c["meta_ctr"]        = c.pop("ctr")
        c["meta_ncpa"]       = c.pop("ncpa")
        c["meta_hook_rate"]  = c.pop("hook_rate")
        c["creative_format"] = c.pop("format")
        c["asset_local_path"] = None
        scored.append(c)

    scored.sort(key=lambda x: x["composite_score"], reverse=True)
    top = scored[: config.META_TOP_N_CREATIVES]

    # Download assets and upsert into DB
    for c in top:
        try:
            local_path = download_asset(
                c["asset_url"],
                c["meta_creative_id"],
                c["creative_format"],
            )
            c["asset_local_path"] = local_path
        except Exception as exc:
            log.warning("Asset download failed for %s: %s", c["meta_creative_id"], exc)

        db.upsert_creative(c)

    log.info("[%s] Scored and staged %d creatives for discovery.", brand, len(top))
    return top
