"""
Meta Marketing API connector.

Responsibilities
----------------
- Pull top-performing creatives from the Meta ad account over a configurable
  lookback window.
- Download asset files (images / videos) to a local temp directory so they
  can be re-uploaded to Pinterest (Meta CDN is not cross-platform shareable).

API surface used
----------------
  GET /{ad_account_id}/ads          – list ads with insights
  GET /{creative_id}                – fetch AdCreative details + asset URLs
  GET /{video_id}                   – fetch video download URL

Requires
--------
  META_ACCESS_TOKEN   – a long-lived system user token with ads_read scope
  META_AD_ACCOUNT_ID  – e.g. act_123456789
"""

import logging
import os
import tempfile
import time
from pathlib import Path
from typing import Any

import requests

import config

log = logging.getLogger(__name__)

_BASE_URL = "https://graph.facebook.com/v19.0"
_TIMEOUT  = 30


def _get(endpoint: str, params: dict | None = None) -> dict:
    params = params or {}
    params["access_token"] = config.META_ACCESS_TOKEN
    resp = requests.get(f"{_BASE_URL}/{endpoint}", params=params, timeout=_TIMEOUT)
    resp.raise_for_status()
    return resp.json()


def _paginate(endpoint: str, params: dict | None = None) -> list[dict]:
    params = params or {}
    results: list[dict] = []
    data = _get(endpoint, params)
    results.extend(data.get("data", []))
    while paging := data.get("paging", {}).get("next"):
        time.sleep(0.2)
        resp = requests.get(paging, timeout=_TIMEOUT)
        resp.raise_for_status()
        data = resp.json()
        results.extend(data.get("data", []))
    return results


def fetch_top_creatives(
    brand_ad_account: str | None = None,
    lookback_days: int = config.META_CREATIVE_LOOKBACK_DAYS,
    min_spend: float = config.META_CREATIVE_MIN_SPEND_USD,
    top_n: int = config.META_TOP_N_CREATIVES,
) -> list[dict]:
    """
    Return top N creatives sorted by composite score for the given lookback.

    Each item:
    {
        "meta_creative_id": str,
        "name": str,
        "spend": float,
        "roas": float,
        "ctr": float,
        "ncpa": float,
        "hook_rate": float,    # video 3-sec / impressions; 0 for static
        "format": str,         # "video" | "image"
        "asset_url": str,
    }
    """
    account = brand_ad_account or config.META_AD_ACCOUNT_ID

    ads = _paginate(
        f"{account}/ads",
        {
            "fields": (
                "id,name,creative{id,name,object_type,video_id,"
                "image_url,image_hash,thumbnail_url},"
                "insights.date_preset(last_n_days_28)"
                "{spend,actions,action_values,impressions,clicks,"
                "video_thruplay_watched_actions,video_play_actions}"
            ),
            "effective_status": '["ACTIVE","PAUSED"]',
            "limit": 200,
        },
    )

    creatives: list[dict] = []
    for ad in ads:
        insights_data = ad.get("insights", {}).get("data", [])
        if not insights_data:
            continue
        ins = insights_data[0]

        spend = float(ins.get("spend", 0))
        if spend < min_spend:
            continue

        impressions   = int(ins.get("impressions", 0) or 0)
        clicks        = int(ins.get("clicks", 0) or 0)
        ctr           = clicks / impressions if impressions else 0.0

        def _action_sum(actions: list, action_type: str) -> float:
            return sum(
                float(a["value"]) for a in actions if a["action_type"] == action_type
            )

        actions       = ins.get("actions", [])
        action_values = ins.get("action_values", [])
        conversions   = _action_sum(actions, "purchase")
        revenue       = _action_sum(action_values, "purchase")
        roas          = revenue / spend if spend else 0.0
        ncpa          = spend / conversions if conversions else 0.0

        # Hook rate: 3-second video plays / impressions
        play_3s = _action_sum(ins.get("video_play_actions", []), "video_view")
        hook_rate = play_3s / impressions if impressions else 0.0

        creative = ad.get("creative", {})
        obj_type  = creative.get("object_type", "IMAGE").upper()
        fmt       = "video" if "VIDEO" in obj_type else "image"
        video_id  = creative.get("video_id")
        asset_url = creative.get("image_url") or creative.get("thumbnail_url") or ""

        if fmt == "video" and video_id:
            try:
                vdata = _get(video_id, {"fields": "download_url,source"})
                asset_url = vdata.get("download_url") or vdata.get("source") or asset_url
            except Exception as exc:
                log.warning("Could not fetch video URL for %s: %s", video_id, exc)

        creatives.append(
            {
                "meta_creative_id": creative.get("id", ad["id"]),
                "name":             ad.get("name", ""),
                "spend":            spend,
                "roas":             roas,
                "ctr":              ctr,
                "ncpa":             ncpa,
                "hook_rate":        hook_rate,
                "format":           fmt,
                "asset_url":        asset_url,
            }
        )

    creatives.sort(key=lambda c: c["roas"], reverse=True)
    return creatives[:top_n]


def download_asset(asset_url: str, creative_id: str, fmt: str) -> str:
    """
    Download a Meta asset to a local temp file.
    Returns the local file path.
    """
    ext = ".mp4" if fmt == "video" else ".jpg"
    dest_dir = Path(tempfile.gettempdir()) / "pambs_assets"
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / f"{creative_id}{ext}"

    if dest.exists():
        return str(dest)

    log.info("Downloading Meta asset %s → %s", creative_id, dest)
    resp = requests.get(asset_url, stream=True, timeout=120)
    resp.raise_for_status()
    with open(dest, "wb") as fh:
        for chunk in resp.iter_content(chunk_size=1 << 20):
            fh.write(chunk)
    return str(dest)
