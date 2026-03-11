"""
Creative staging module.

Fetches Meta creatives for discovery launches.

Selection logic
---------------
- Pull up to META_TOP_N_CREATIVES (30) ads from Meta, deduplicated by name,
  sorted by 7-day spend descending, with a minimum of $500 spend.
- Exclude any creative (by meta_creative_id) already staged in a Pinterest
  discovery campaign or already graduated to scale.
- Download assets locally and upsert into creative_mirror for tracking.

No composite scoring is applied — we test by spend volume to let Pinterest's
own optimisation determine winners.
"""

import logging
from datetime import date

import config
from connectors.meta_connector import fetch_top_creatives, download_asset
from state import db

log = logging.getLogger(__name__)


def fetch_and_score_meta_creatives(brand: str) -> list[dict]:
    """
    Pull Meta creatives for this brand, filter already-launched ones,
    download assets, upsert into creative_mirror, and return the list
    ready for Pinterest launch (sorted by spend descending).
    """
    already_pending    = db.get_unmirrored_creative_ids(brand)
    already_graduated  = db.get_graduated_creative_ids(brand)
    exclude = already_pending | already_graduated

    raw = fetch_top_creatives(
        lookback_days=config.META_CREATIVE_LOOKBACK_DAYS,
        min_spend=config.META_CREATIVE_MIN_SPEND_USD,
        top_n=config.META_TOP_N_CREATIVES,
    )

    candidates = [c for c in raw if c["meta_creative_id"] not in exclude]

    if not candidates:
        log.info("[%s] No new Meta creatives qualify for mirroring.", brand)
        return []

    today = date.today().isoformat()
    staged: list[dict] = []
    for c in candidates:
        c["brand"]            = brand
        c["first_seen_date"]  = today
        c["last_scored_date"] = today
        c["composite_score"]  = 0.0   # not scored; ordered by spend
        c["meta_spend_usd"]   = c.pop("spend")
        c["meta_roas"]        = c.pop("roas")
        c["meta_ctr"]         = c.pop("ctr")
        c["meta_ncpa"]        = c.pop("ncpa")
        c["meta_hook_rate"]   = c.pop("hook_rate")
        c["creative_format"]  = c.pop("format")
        c["asset_local_path"] = None

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
        staged.append(c)

    log.info("[%s] Staged %d new Meta creatives for discovery (sorted by spend).", brand, len(staged))
    return staged
