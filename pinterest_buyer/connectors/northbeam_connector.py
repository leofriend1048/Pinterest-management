"""
Northbeam API connector.

Responsibilities
----------------
- Fetch Pinterest-attributed ROAS, spend, and revenue per campaign
  for the budget engine.
- Fetch creative-level attribution when available; fall back to platform
  ROAS from the Pinterest connector if Northbeam creative-level data
  isn't offered at this org's tier.

Configuration notes (confirm with CSM before Phase 1)
------------------------------------------------------
  • Endpoint availability for platform-filtered ROAS
  • Data freshness latency (T+1 vs T+0)
  • Whether creative-level attribution is available via API

API surface (Northbeam REST v2)
-------------------------------
  GET /v2/attribution/campaigns  – campaign-level performance
  GET /v2/attribution/ads        – ad/creative-level performance (if available)

Headers
-------
  X-API-Key: <NORTHBEAM_API_KEY>
  X-Org-Id:  <NORTHBEAM_ORG_ID>
"""

import logging
from datetime import date, timedelta

import requests

import config

log = logging.getLogger(__name__)

_BASE_URL = "https://api.northbeam.io"
_TIMEOUT  = 30


def _headers() -> dict:
    return {
        "X-API-Key": config.NORTHBEAM_API_KEY,
        "X-Org-Id":  config.NORTHBEAM_ORG_ID,
        "Accept":    "application/json",
    }


def _get(path: str, params: dict | None = None) -> dict:
    resp = requests.get(
        f"{_BASE_URL}{path}",
        headers=_headers(),
        params=params or {},
        timeout=_TIMEOUT,
    )
    resp.raise_for_status()
    return resp.json()


def fetch_campaign_roas(
    lookback_days: int = config.DECISION_LOOKBACK_DAYS,
) -> dict[str, dict]:
    """
    Return a mapping of Pinterest campaign ID → attribution metrics,
    filtered to the Pinterest channel.

    Returns
    -------
    {
        "<pinterest_campaign_id>": {
            "spend": float,
            "revenue": float,
            "roas": float,
            "conversions": int,
        },
        ...
    }
    """
    end_date   = date.today() - timedelta(days=1)   # T-1 (yesterday)
    start_date = end_date - timedelta(days=lookback_days - 1)

    try:
        data = _get(
            "/v2/attribution/campaigns",
            {
                "channel":    "pinterest",
                "start_date": start_date.isoformat(),
                "end_date":   end_date.isoformat(),
                "granularity": "total",
            },
        )
    except requests.HTTPError as exc:
        log.error("Northbeam campaign fetch failed: %s", exc)
        return {}

    result: dict[str, dict] = {}
    for row in data.get("data", []):
        campaign_id = str(row.get("campaign_id") or row.get("id", ""))
        if not campaign_id:
            continue
        spend   = float(row.get("spend", 0))
        revenue = float(row.get("revenue", 0))
        result[campaign_id] = {
            "spend":       spend,
            "revenue":     revenue,
            "roas":        revenue / spend if spend else 0.0,
            "conversions": int(row.get("conversions", 0)),
        }

    log.info("Northbeam: fetched ROAS for %d campaigns", len(result))
    return result


def fetch_ad_roas(
    lookback_days: int = config.DECISION_LOOKBACK_DAYS,
) -> dict[str, dict]:
    """
    Return ad-level attribution from Northbeam if available.
    Falls back to an empty dict (caller should use Pinterest platform data).

    Returns
    -------
    { "<pinterest_ad_id>": {"spend": float, "revenue": float, "roas": float} }
    """
    end_date   = date.today() - timedelta(days=1)
    start_date = end_date - timedelta(days=lookback_days - 1)

    try:
        data = _get(
            "/v2/attribution/ads",
            {
                "channel":    "pinterest",
                "start_date": start_date.isoformat(),
                "end_date":   end_date.isoformat(),
                "granularity": "total",
            },
        )
    except requests.HTTPError as exc:
        status = exc.response.status_code if exc.response is not None else "?"
        if status == 404:
            log.info("Northbeam creative-level attribution not available for this org tier.")
        else:
            log.error("Northbeam ad fetch failed: %s", exc)
        return {}

    result: dict[str, dict] = {}
    for row in data.get("data", []):
        ad_id = str(row.get("ad_id") or row.get("id", ""))
        if not ad_id:
            continue
        spend   = float(row.get("spend", 0))
        revenue = float(row.get("revenue", 0))
        result[ad_id] = {
            "spend":   spend,
            "revenue": revenue,
            "roas":    revenue / spend if spend else 0.0,
        }

    log.info("Northbeam: fetched ad-level ROAS for %d ads", len(result))
    return result


def fetch_multiwindow_campaign_roas(
    windows: tuple[int, ...] = (1, 3, 7),
) -> dict[int, dict[str, dict]]:
    """
    Fetch Northbeam campaign ROAS for multiple lookback windows in one call set.

    Returns
    -------
    {
        1: {"<campaign_id>": {"spend": float, "roas": float, ...}},
        3: {...},
        7: {...},
    }
    """
    result: dict[int, dict[str, dict]] = {}
    for days in windows:
        result[days] = fetch_campaign_roas(lookback_days=days)
    return result


def fetch_multiwindow_ad_roas(
    windows: tuple[int, ...] = (1, 3, 7),
) -> dict[int, dict[str, dict]]:
    """
    Fetch Northbeam ad-level ROAS for multiple lookback windows.

    Returns
    -------
    {1: {"<ad_id>": {...}}, 3: {...}, 7: {...}}
    """
    result: dict[int, dict[str, dict]] = {}
    for days in windows:
        result[days] = fetch_ad_roas(lookback_days=days)
    return result
