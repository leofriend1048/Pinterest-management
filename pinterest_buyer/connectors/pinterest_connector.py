"""
Pinterest Marketing API connector.

Responsibilities
----------------
- Asset upload (image or video)
- Campaign / ad group / ad creation
- Ad-level pause / resume
- Campaign budget PATCH
- Campaign and ad-level performance pulls

Rate limit: 1,000 req/hour — well within usage for 15 campaigns.

API reference: https://developers.pinterest.com/docs/api/v5/
"""

import logging
import time
from pathlib import Path
from typing import Any

import requests

import config

log = logging.getLogger(__name__)

_BASE     = "https://api.pinterest.com/v5"
_TIMEOUT  = 30
_RETRY_ON = {429, 500, 502, 503, 504}


def _headers() -> dict:
    return {
        "Authorization": f"Bearer {config.PINTEREST_ACCESS_TOKEN}",
        "Content-Type":  "application/json",
    }


def _req(method: str, path: str, **kwargs) -> dict:
    url = f"{_BASE}{path}"
    for attempt in range(4):
        resp = requests.request(method, url, headers=_headers(), timeout=_TIMEOUT, **kwargs)
        if resp.status_code == 429:
            wait = int(resp.headers.get("Retry-After", 60))
            log.warning("Pinterest rate-limited — sleeping %ds", wait)
            time.sleep(wait)
            continue
        if resp.status_code in _RETRY_ON and attempt < 3:
            time.sleep(2 ** attempt)
            continue
        resp.raise_for_status()
        return resp.json() if resp.content else {}
    raise RuntimeError(f"Pinterest API request failed after 4 attempts: {method} {path}")


# ── Asset upload ──────────────────────────────────────────────────────────────

def upload_image(local_path: str) -> str:
    """Upload an image file and return the media_id."""
    register = _req(
        "POST", f"/ad_accounts/{config.PINTEREST_AD_ACCOUNT_ID}/media",
        json={"media_type": "image"},
    )
    upload_url  = register["upload_url"]
    upload_params = register.get("upload_parameters", {})
    media_id    = register["media_id"]

    with open(local_path, "rb") as fh:
        fields = {k: (None, v) for k, v in upload_params.items()}
        fields["file"] = (Path(local_path).name, fh, "image/jpeg")
        resp = requests.post(upload_url, files=fields, timeout=120)
        resp.raise_for_status()

    log.info("Pinterest: uploaded image %s → media_id=%s", local_path, media_id)
    return media_id


def upload_video(local_path: str) -> str:
    """Register + upload a video and return the media_id (polls until ready)."""
    register = _req(
        "POST", f"/ad_accounts/{config.PINTEREST_AD_ACCOUNT_ID}/media",
        json={"media_type": "video"},
    )
    upload_url  = register["upload_url"]
    upload_params = register.get("upload_parameters", {})
    media_id    = register["media_id"]

    with open(local_path, "rb") as fh:
        fields = {k: (None, v) for k, v in upload_params.items()}
        fields["file"] = (Path(local_path).name, fh, "video/mp4")
        resp = requests.post(upload_url, files=fields, timeout=300)
        resp.raise_for_status()

    # Poll until processing completes (max 10 minutes)
    for _ in range(60):
        status = _req("GET", f"/ad_accounts/{config.PINTEREST_AD_ACCOUNT_ID}/media/{media_id}")
        if status.get("status") == "succeeded":
            break
        if status.get("status") == "failed":
            raise RuntimeError(f"Pinterest video processing failed: {media_id}")
        time.sleep(10)

    log.info("Pinterest: uploaded video %s → media_id=%s", local_path, media_id)
    return media_id


# ── Campaign management ───────────────────────────────────────────────────────

def create_campaign(
    name: str,
    daily_budget_usd: float,
    objective: str = "CATALOG_SALES",
) -> str:
    """Create a campaign and return its campaign_id."""
    body = {
        "name":           name,
        "objective_type": objective,
        "status":         "ACTIVE",
        "daily_spend_cap": int(daily_budget_usd * 1_000_000),  # micro-USD
        "ad_account_id":  config.PINTEREST_AD_ACCOUNT_ID,
    }
    resp = _req("POST", f"/ad_accounts/{config.PINTEREST_AD_ACCOUNT_ID}/campaigns", json=body)
    campaign_id = resp["items"][0]["id"]
    log.info("Pinterest: created campaign '%s' id=%s", name, campaign_id)
    return campaign_id


def update_campaign_budget(campaign_id: str, new_daily_budget_usd: float) -> None:
    body = {
        "id":             campaign_id,
        "daily_spend_cap": int(new_daily_budget_usd * 1_000_000),
    }
    _req(
        "PATCH",
        f"/ad_accounts/{config.PINTEREST_AD_ACCOUNT_ID}/campaigns",
        json={"campaigns": [body]},
    )
    log.info("Pinterest: updated campaign %s budget → $%.0f/day", campaign_id, new_daily_budget_usd)


def pause_campaign(campaign_id: str) -> None:
    body = {"id": campaign_id, "status": "PAUSED"}
    _req(
        "PATCH",
        f"/ad_accounts/{config.PINTEREST_AD_ACCOUNT_ID}/campaigns",
        json={"campaigns": [body]},
    )
    log.info("Pinterest: paused campaign %s", campaign_id)


# ── Ad group management ───────────────────────────────────────────────────────

def create_ad_group(
    campaign_id: str,
    name: str,
    targeting_spec: dict,
    daily_budget_usd: float | None = None,
) -> str:
    """
    Create an ad group with ROAS optimization and auto bidding.

    ROAS optimization is always applied (Pinterest Performance+ Bidding).
    Requires PINTEREST_CONVERSION_TAG_ID in env for full attribution; logs a
    warning if missing but still creates the ad group.
    """
    body: dict[str, Any] = {
        "campaign_id":       campaign_id,
        "name":              name,
        "billable_event":    "CLICKTHROUGH",
        "targeting_spec":    targeting_spec,
        "status":            "ACTIVE",
        "ad_account_id":     config.PINTEREST_AD_ACCOUNT_ID,
        "bid_strategy_type": "AUTOMATIC_BID",
    }

    # ROAS optimization — always on.  Conversion tag required for attribution.
    if config.PINTEREST_CONVERSION_TAG_ID:
        body["optimization_goal_metadata"] = {
            "conversion_tag_v3_goal_metadata": {
                "conversion_event":   "checkout",
                "conversion_tag_id":  config.PINTEREST_CONVERSION_TAG_ID,
                "is_roas_optimized":  True,
                "learning_mode_type": "ACTIVE",
            }
        }
    else:
        log.warning(
            "PINTEREST_CONVERSION_TAG_ID not set — ROAS optimization active but "
            "conversion attribution will use last-click only.  Add the tag ID to .env."
        )
        body["optimization_goal_metadata"] = {
            "conversion_tag_v3_goal_metadata": {
                "conversion_event":  "checkout",
                "is_roas_optimized": True,
                "learning_mode_type": "ACTIVE",
            }
        }

    if daily_budget_usd:
        body["daily_spend_cap"] = int(daily_budget_usd * 1_000_000)

    resp = _req(
        "POST",
        f"/ad_accounts/{config.PINTEREST_AD_ACCOUNT_ID}/ad_groups",
        json=body,
    )
    ad_group_id = resp["items"][0]["id"]
    log.info("Pinterest: created ad group '%s' id=%s", name, ad_group_id)
    return ad_group_id


# ── Ad management ─────────────────────────────────────────────────────────────

def create_ad(
    ad_group_id: str,
    name: str,
    pin_id: str | None = None,
    media_id: str | None = None,
    link: str = "https://example.com",
    call_to_action: str = "SHOP_NOW",
    title: str = "",
    description: str = "",
) -> str:
    """
    Create an ad within an ad group. Returns ad_id.

    - call_to_action: Pinterest CTA button (SHOP_NOW, LEARN_MORE, etc.)
    - title / description: pin title and description for new promoted pins.
    - Thumbnail is auto-selected by Pinterest (best frame for video).
    """
    creative: dict[str, Any] = {"ad_account_id": config.PINTEREST_AD_ACCOUNT_ID}
    if pin_id:
        creative["pin_id"] = pin_id
    elif media_id:
        creative["media_id"] = media_id

    body: dict[str, Any] = {
        "ad_group_id":   ad_group_id,
        "creative_type": "REGULAR",
        "name":          name,
        "status":        "ACTIVE",
        "link":          link,
        "call_to_action": call_to_action,
        **creative,
    }
    if title:
        body["title"] = title
    if description:
        body["description"] = description

    resp = _req(
        "POST",
        f"/ad_accounts/{config.PINTEREST_AD_ACCOUNT_ID}/ads",
        json=[body],   # API expects an array
    )
    ad_id = resp["items"][0]["id"]
    log.info("Pinterest: created ad '%s' id=%s in ad_group=%s", name, ad_id, ad_group_id)
    return ad_id


def pause_ad(ad_id: str) -> None:
    body = {"id": ad_id, "status": "PAUSED"}
    _req(
        "PATCH",
        f"/ad_accounts/{config.PINTEREST_AD_ACCOUNT_ID}/ads",
        json={"ads": [body]},
    )
    log.info("Pinterest: paused ad %s", ad_id)


def resume_ad(ad_id: str) -> None:
    body = {"id": ad_id, "status": "ACTIVE"}
    _req(
        "PATCH",
        f"/ad_accounts/{config.PINTEREST_AD_ACCOUNT_ID}/ads",
        json={"ads": [body]},
    )
    log.info("Pinterest: resumed ad %s", ad_id)


# ── Performance data ──────────────────────────────────────────────────────────

def get_campaign_analytics(
    campaign_ids: list[str],
    start_date: str,
    end_date: str,
) -> list[dict]:
    """
    Fetch campaign-level analytics.

    Returns list of {campaign_id, spend, impressions, clicks, ctr, ...}.
    Attribution windows match Northbeam: 1-day click + 1-day view.
    """
    ids_param = ",".join(campaign_ids)
    resp = _req(
        "GET",
        f"/ad_accounts/{config.PINTEREST_AD_ACCOUNT_ID}/campaigns/analytics",
        params={
            "campaign_ids":        ids_param,
            "start_date":          start_date,
            "end_date":            end_date,
            "columns":             (
                "CAMPAIGN_ID,SPEND_IN_DOLLAR,TOTAL_CONVERSIONS,"
                "TOTAL_WEB_CHECKOUT_VALUE_IN_MICRO_DOLLAR,WEB_CHECKOUT_ROAS,"
                "PAID_IMPRESSION,CLICKTHROUGH_1,"
                "VIDEO_3SEC_VIEWS_2,VIDEO_P0_COMBINED_2"
            ),
            "granularity":         "TOTAL",
            "click_window_days":   config.CLICK_WINDOW_DAYS,
            "view_window_days":    config.VIEW_WINDOW_DAYS,
        },
    )
    results = []
    for row in resp if isinstance(resp, list) else []:
        spend       = float(row.get("SPEND_IN_DOLLAR", 0) or 0)
        revenue     = float(row.get("TOTAL_WEB_CHECKOUT_VALUE_IN_MICRO_DOLLAR", 0) or 0) / 1_000_000
        roas        = float(row.get("WEB_CHECKOUT_ROAS", 0) or 0) or (revenue / spend if spend else 0.0)
        imps        = int(row.get("PAID_IMPRESSION", 0) or 0)
        clicks      = int(row.get("CLICKTHROUGH_1", 0) or 0)
        conversions = int(row.get("TOTAL_CONVERSIONS", 0) or 0)
        plays_3s    = int(row.get("VIDEO_3SEC_VIEWS_2", 0) or 0)
        plays_all   = int(row.get("VIDEO_P0_COMBINED_2", 0) or 0)
        results.append(
            {
                "campaign_id": row.get("CAMPAIGN_ID"),
                "spend":       spend,
                "revenue":     revenue,
                "roas":        roas,
                "impressions": imps,
                "clicks":      clicks,
                "ctr":         clicks / imps if imps else 0.0,
                "conversions": conversions,
                "ncpa":        spend / conversions if conversions else 0.0,
                "hook_rate":   plays_3s / plays_all if plays_all else 0.0,
            }
        )
    return results


def get_ad_analytics(
    ad_ids: list[str],
    start_date: str,
    end_date: str,
) -> list[dict]:
    """Fetch ad-level analytics for frequency + ROAS decay checks."""
    ids_param = ",".join(ad_ids)
    resp = _req(
        "GET",
        f"/ad_accounts/{config.PINTEREST_AD_ACCOUNT_ID}/ads/analytics",
        params={
            "ad_ids":            ids_param,
            "start_date":        start_date,
            "end_date":          end_date,
            "columns":           (
                "AD_ID,SPEND_IN_DOLLAR,TOTAL_CONVERSIONS,"
                "TOTAL_WEB_CHECKOUT_VALUE_IN_MICRO_DOLLAR,WEB_CHECKOUT_ROAS,"
                "PAID_IMPRESSION,CLICKTHROUGH_1,FREQUENCY,"
                "VIDEO_3SEC_VIEWS_2,VIDEO_P0_COMBINED_2"
            ),
            "granularity":       "TOTAL",
            "click_window_days": config.CLICK_WINDOW_DAYS,
            "view_window_days":  config.VIEW_WINDOW_DAYS,
        },
    )
    results = []
    for row in resp if isinstance(resp, list) else []:
        spend       = float(row.get("SPEND_IN_DOLLAR", 0) or 0)
        revenue     = float(row.get("TOTAL_WEB_CHECKOUT_VALUE_IN_MICRO_DOLLAR", 0) or 0) / 1_000_000
        roas        = float(row.get("WEB_CHECKOUT_ROAS", 0) or 0) or (revenue / spend if spend else 0.0)
        imps        = int(row.get("PAID_IMPRESSION", 0) or 0)
        clicks      = int(row.get("CLICKTHROUGH_1", 0) or 0)
        conversions = int(row.get("TOTAL_CONVERSIONS", 0) or 0)
        frequency   = float(row.get("FREQUENCY", 0) or 0)
        plays_3s    = int(row.get("VIDEO_3SEC_VIEWS_2", 0) or 0)
        plays_all   = int(row.get("VIDEO_P0_COMBINED_2", 0) or 0)
        results.append(
            {
                "ad_id":       row.get("AD_ID"),
                "spend":       spend,
                "revenue":     revenue,
                "roas":        roas,
                "impressions": imps,
                "clicks":      clicks,
                "ctr":         clicks / imps if imps else 0.0,
                "conversions": conversions,
                "ncpa":        spend / conversions if conversions else 0.0,
                "frequency":   frequency,
                "hook_rate":   plays_3s / plays_all if plays_all else 0.0,
            }
        )
    return results


def list_ads_in_campaign(campaign_id: str) -> list[dict]:
    """Return all ads (id, name, status) within a campaign."""
    resp = _req(
        "GET",
        f"/ad_accounts/{config.PINTEREST_AD_ACCOUNT_ID}/ads",
        params={"campaign_ids": campaign_id, "page_size": 250},
    )
    return resp.get("items", [])


def list_campaigns() -> list[dict]:
    """Return all campaigns (handles pagination)."""
    items: list[dict] = []
    params: dict = {"page_size": 250}
    while True:
        resp = _req(
            "GET",
            f"/ad_accounts/{config.PINTEREST_AD_ACCOUNT_ID}/campaigns",
            params=params,
        )
        items.extend(resp.get("items", []))
        bookmark = resp.get("bookmark")
        if not bookmark:
            break
        params["bookmark"] = bookmark
    return items


def get_hourly_campaign_analytics(
    campaign_ids: list[str],
    date_str: str,
) -> list[dict]:
    """
    Fetch HOUR-granularity analytics for a single day (for dayparting).

    Pinterest HOUR granularity constraints:
      - max 8 days back
      - max 3-day range (use a single date for one day)

    Returns list of {campaign_id, hour_utc, spend, revenue}.
    """
    ids_param = ",".join(campaign_ids)
    resp = _req(
        "GET",
        f"/ad_accounts/{config.PINTEREST_AD_ACCOUNT_ID}/campaigns/analytics",
        params={
            "campaign_ids":      ids_param,
            "start_date":        date_str,
            "end_date":          date_str,
            "columns":           "CAMPAIGN_ID,SPEND_IN_DOLLAR,TOTAL_WEB_CHECKOUT_VALUE_IN_MICRO_DOLLAR",
            "granularity":       "HOUR",
            "click_window_days": config.CLICK_WINDOW_DAYS,
            "view_window_days":  config.VIEW_WINDOW_DAYS,
        },
    )
    results = []
    for row in resp if isinstance(resp, list) else []:
        spend   = float(row.get("SPEND_IN_DOLLAR", 0) or 0)
        revenue = float(row.get("TOTAL_WEB_CHECKOUT_VALUE_IN_MICRO_DOLLAR", 0) or 0) / 1_000_000
        # DATE field from HOUR granularity is "YYYY-MM-DD HH:MM:SS" — extract hour
        date_field = row.get("DATE", "")
        try:
            hour_utc = int(date_field[11:13]) if len(date_field) >= 13 else 0
        except (ValueError, IndexError):
            hour_utc = 0
        results.append(
            {
                "campaign_id": row.get("CAMPAIGN_ID"),
                "hour_utc":    hour_utc,
                "spend":       spend,
                "revenue":     revenue,
            }
        )
    return results


def get_keyword_analytics(
    ad_ids: list[str],
    start_date: str,
    end_date: str,
) -> list[dict]:
    """
    Fetch keyword-level targeting analytics for a list of ads.
    Uses the targeting_analytics endpoint with targeting_types=KEYWORD.

    Returns list of {keyword, spend, impressions, clicks, conversions, revenue}.
    """
    results: list[dict] = []
    for ad_id in ad_ids:
        try:
            resp = _req(
                "GET",
                f"/ad_accounts/{config.PINTEREST_AD_ACCOUNT_ID}/ads/{ad_id}/targeting_analytics",
                params={
                    "start_date":      start_date,
                    "end_date":        end_date,
                    "targeting_types": "KEYWORD",
                    "columns":         (
                        "SPEND_IN_DOLLAR,TOTAL_CONVERSIONS,"
                        "TOTAL_WEB_CHECKOUT_VALUE_IN_MICRO_DOLLAR,"
                        "PAID_IMPRESSION,CLICKTHROUGH_1"
                    ),
                    "granularity":     "TOTAL",
                },
            )
            for row in resp.get("data", []) if isinstance(resp, dict) else []:
                kw = (row.get("targeting_value") or "").strip()
                if not kw:
                    continue
                spend       = float(row.get("SPEND_IN_DOLLAR", 0) or 0)
                revenue     = float(row.get("TOTAL_WEB_CHECKOUT_VALUE_IN_MICRO_DOLLAR", 0) or 0) / 1_000_000
                imps        = int(row.get("PAID_IMPRESSION", 0) or 0)
                clicks      = int(row.get("CLICKTHROUGH_1", 0) or 0)
                conversions = int(row.get("TOTAL_CONVERSIONS", 0) or 0)
                results.append(
                    {
                        "keyword":     kw,
                        "spend":       spend,
                        "revenue":     revenue,
                        "impressions": imps,
                        "clicks":      clicks,
                        "conversions": conversions,
                        "roas":        revenue / spend if spend else 0.0,
                    }
                )
        except Exception as exc:
            log.warning("Keyword analytics failed for ad %s: %s", ad_id, exc)
    return results
