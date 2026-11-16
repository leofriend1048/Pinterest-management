"""Media-buyer tools: the ad account in the words a buyer uses.

The generic tools in server.py can reach every endpoint, but they speak
Pinterest's dialect: operation ids, micro-dollars, report column names. These
tools speak the buyer's: spend, ROAS, CPA, hook rate, "the ad set", "last 7
days", dollars per day.
"""

from __future__ import annotations

import functools
import json
import math
import webbrowser
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Literal

from . import credentials, dashboard, reports
from mcp.server.fastmcp import Image

from .client import PinterestError, expand_files, upload_video
from .server import READ, WRITE, LOCAL, _render, client, mcp

MICRO = 1_000_000
CONTEXT_FILE = "context.md"

DateRange = Literal[
    "today",
    "yesterday",
    "last_3_days",
    "last_7_days",
    "last_14_days",
    "last_30_days",
    "last_90_days",
    "this_month",
    "last_month",
    "custom",
]
Level = Literal["account", "campaign", "ad_group", "ad"]
Segment = Literal[
    "none",
    "age",
    "gender",
    "age_and_gender",
    "keyword",
    "interest",
    "audience",
    "placement",
    "device",
    "region",
    "country",
    "media_type",
]

LEVELS = {
    "account": ("ADVERTISER", []),
    "campaign": ("CAMPAIGN", ["CAMPAIGN_ID", "CAMPAIGN_NAME", "CAMPAIGN_ENTITY_STATUS"]),
    "ad_group": (
        "AD_GROUP",
        ["AD_GROUP_ID", "AD_GROUP_NAME", "AD_GROUP_ENTITY_STATUS", "CAMPAIGN_ID", "CAMPAIGN_NAME"],
    ),
    "ad": (
        "PIN_PROMOTION",
        ["AD_ID", "AD_NAME", "PIN_ID", "AD_GROUP_ID", "AD_GROUP_NAME", "CAMPAIGN_ID", "CAMPAIGN_NAME"],
    ),
}
SEGMENTS = {
    "age": "AGE_BUCKET",
    "gender": "GENDER",
    "age_and_gender": "AGE_BUCKET_AND_GENDER",
    "keyword": "KEYWORD",
    "interest": "TARGETED_INTEREST",
    "audience": "AUDIENCE_INCLUDE",
    "placement": "PLACEMENT",
    "device": "APPTYPE",
    "region": "REGION",
    "country": "COUNTRY",
    "media_type": "MEDIA_TYPE",
}
METRICS = [
    "SPEND_IN_DOLLAR",
    "IMPRESSION_1",
    "CLICKTHROUGH_1",
    "OUTBOUND_CLICK_1",
    "TOTAL_PAGE_VISIT",
    "TOTAL_ADD_TO_CART",
    "TOTAL_CHECKOUT",
    "TOTAL_CHECKOUT_VALUE_IN_MICRO_DOLLAR",
]
VIDEO_METRICS = [
    "VIDEO_3SEC_VIEWS_1",
    "VIDEO_P25_COMBINED_1",
    "VIDEO_P50_COMBINED_1",
    "VIDEO_P75_COMBINED_1",
    "VIDEO_P95_COMBINED_1",
]
SORTABLE = Literal[
    "spend", "roas", "cpa", "revenue", "purchases", "ctr", "cpc", "cpm", "impressions",
    "hook_rate", "hold_rate", "name", "date",
]
DEFINITIONS = {
    "spend / revenue / cpa / cpc / cpm / aov": "US dollars",
    "impressions, clicks": "paid only (earned activity from saves is excluded)",
    "clicks": "outbound clicks to the site; pin_clicks are clicks on the Pin itself",
    "ctr": "outbound clicks / impressions, percent",
    "purchases / revenue": "Pinterest-attributed checkouts and their value",
    "roas": "revenue / spend, as Pinterest reports it. Other attribution tools will differ.",
    "cvr": "purchases / outbound clicks, percent",
    "hook_rate": "3-second video views / impressions, percent",
    "hold_rate": "views reaching 95% of the video / 3-second views, percent",
    "confidence": "how far to trust this row's ROAS and CPA: low (<10 purchases), "
                  "directional (10-29), solid (30+). Do not call winners or losers on low.",
}


# ── helpers ───────────────────────────────────────────────────────────────────


def resolve_dates(
    date_range: str, start_date: str | None, end_date: str | None, today: date | None = None
) -> tuple[str, str]:
    today = today or date.today()
    yesterday = today - timedelta(days=1)
    if start_date or end_date or date_range == "custom":
        if not (start_date and end_date):
            raise ValueError("A custom range needs both start_date and end_date (YYYY-MM-DD).")
        return start_date, end_date
    if date_range == "today":
        return today.isoformat(), today.isoformat()
    if date_range == "yesterday":
        return yesterday.isoformat(), yesterday.isoformat()
    if date_range.startswith("last_") and date_range.endswith("_days"):
        days = int(date_range.split("_")[1])
        # Complete days only: "last 7 days" ends yesterday.
        return (today - timedelta(days=days)).isoformat(), yesterday.isoformat()
    if date_range == "this_month":
        return today.replace(day=1).isoformat(), today.isoformat()
    if date_range == "last_month":
        last = today.replace(day=1) - timedelta(days=1)
        return last.replace(day=1).isoformat(), last.isoformat()
    raise ValueError(f"Unknown date_range '{date_range}'.")


def _div(a: float, b: float, scale: float = 1.0, digits: int = 2) -> float | None:
    return round(a / b * scale, digits) if b else None


def summarize(raw: dict, video: bool = False) -> dict:
    """Turn raw report sums into the numbers a buyer reads."""
    spend = float(raw.get("SPEND_IN_DOLLAR") or 0)
    impressions = float(raw.get("IMPRESSION_1") or 0)
    clicks = float(raw.get("OUTBOUND_CLICK_1") or 0)
    purchases = float(raw.get("TOTAL_CHECKOUT") or 0)
    revenue = float(raw.get("TOTAL_CHECKOUT_VALUE_IN_MICRO_DOLLAR") or 0) / MICRO
    out: dict[str, Any] = {
        "spend": round(spend, 2),
        "revenue": round(revenue, 2),
        "roas": _div(revenue, spend),
        "purchases": int(purchases),
        "cpa": _div(spend, purchases),
        "aov": _div(revenue, purchases),
        "impressions": int(impressions),
        "cpm": _div(spend, impressions, 1000),
        "clicks": int(clicks),
        "ctr": _div(clicks, impressions, 100),
        "cpc": _div(spend, clicks),
        "cvr": _div(purchases, clicks, 100),
        "confidence": confidence(purchases),
        "pin_clicks": int(raw.get("CLICKTHROUGH_1") or 0),
        "page_visits": int(raw.get("TOTAL_PAGE_VISIT") or 0),
        "add_to_carts": int(raw.get("TOTAL_ADD_TO_CART") or 0),
    }
    if video:
        views = float(raw.get("VIDEO_3SEC_VIEWS_1") or 0)
        out["video_3s_views"] = int(views)
        out["hook_rate"] = _div(views, impressions, 100)
        out["hold_rate"] = _div(float(raw.get("VIDEO_P95_COMBINED_1") or 0), views, 100)
        for pct in (25, 50, 75, 95):
            out[f"video_p{pct}"] = int(raw.get(f"VIDEO_P{pct}_COMBINED_1") or 0)
    return out


def _name_matches(name: str, wanted: str | None) -> bool:
    if not wanted:
        return True
    name = (name or "").lower()
    return all(word in name for word in wanted.lower().split())


def _list_all(path: str, query: dict, max_pages: int = 12) -> tuple[list[dict], bool]:
    """Page through a list endpoint. Returns (items, truncated)."""
    items: list[dict] = []
    query = {**query, "page_size": 250}
    for _ in range(max_pages):
        page = client().request("GET", path, query=query)
        items.extend(page.get("items") or [])
        if not page.get("bookmark"):
            return items, False
        query["bookmark"] = page["bookmark"]
    return items, True


def _usd(micro: Any) -> float | None:
    return round(int(micro) / MICRO, 2) if micro else None


def _check_exceptions(result: Any) -> None:
    """Bulk updates return HTTP 200 with per-item failures; surface those."""
    problems = []
    for item in (result or {}).get("items", []) if isinstance(result, dict) else []:
        exceptions = item.get("exceptions")
        if exceptions:
            problems.append(exceptions)
    if problems:
        raise ValueError(f"Pinterest rejected the change: {json.dumps(problems)}")


def _wrap(fn):
    """Report Pinterest errors as readable tool errors."""

    @functools.wraps(fn)
    def inner(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except PinterestError as exc:
            raise ValueError(str(exc)) from None

    return inner


def context_path() -> Path:
    return credentials.path().parent / CONTEXT_FILE


# ── context ───────────────────────────────────────────────────────────────────

CONTEXT_TEMPLATE = """\
# Pinterest account context

Notes Claude reads before working in this ad account. Plain language is fine.

## Brand and offer
- Brand:
- Hero product and price:
- Default landing page (with tracking parameters):

## Goals
- Target ROAS:
- Target CPA:
- Attribution windows to report on (click / engagement / view days):

## How the account is organised
- Campaign naming convention:
- Ad group naming convention:
- What each campaign type is for:

## Defaults when building
- Audience (age, gender, geo):
- Starting daily budget:
- Call to action:

## Rules
- Changes that always need a second check:
- Things never to touch:
"""


@mcp.tool(annotations=LOCAL)
def pinterest_account_context() -> str:
    """Read the saved notes about this ad account. Call this first in a session.

    Returns the account's own context: brand, target ROAS and CPA, which
    attribution windows to report on, naming conventions, default landing page,
    audience and budget defaults, and any rules about what not to change. Use
    it to interpret requests ("is this ad good?" means against the target here)
    and to fill in defaults when building campaigns.
    """
    path = context_path()
    if path.exists() and path.read_text().strip():
        return json.dumps({"context_file": str(path), "context": path.read_text()}, indent=1)
    return json.dumps(
        {
            "context_file": str(path),
            "context": None,
            "note": (
                "No account context saved yet. Ask the user for their target ROAS/CPA, "
                "attribution windows and naming conventions, then offer to save them to "
                "context_file using this template."
            ),
            "template": CONTEXT_TEMPLATE,
        },
        indent=1,
    )


# ── reading ───────────────────────────────────────────────────────────────────


@mcp.tool(annotations=READ)
@_wrap
def pinterest_performance(
    level: Level = "campaign",
    date_range: DateRange = "last_7_days",
    start_date: str | None = None,
    end_date: str | None = None,
    breakdown: Literal["total", "day", "week", "month"] = "total",
    segment_by: Segment = "none",
    name_contains: str | None = None,
    campaign_ids: list[str] | None = None,
    ad_group_ids: list[str] | None = None,
    sort_by: SORTABLE = "spend",
    ascending: bool = False,
    min_spend: float = 0,
    limit: int = 50,
    include_video: bool = False,
    click_window_days: Literal[0, 1, 7, 14, 30, 60] | None = None,
    engagement_window_days: Literal[0, 1, 7, 14, 30, 60] | None = None,
    view_window_days: Literal[0, 1, 7, 14, 30, 60] | None = None,
) -> str:
    """Performance for the account, campaigns, ad groups (ad sets) or ads, in dollars.

    The tool for "how are we doing", "what's my ROAS", "which ads are winning",
    "what should I turn off", "how did yesterday go". Returns spend, revenue,
    ROAS, purchases, CPA, AOV, impressions, CPM, clicks, CTR, CPC, CVR, page
    visits and add-to-carts per row, plus account totals for the same filter.

    - level: "account", "campaign", "ad_group" (what Meta calls an ad set) or
      "ad" (a creative).
    - date_range: a preset, or pass start_date and end_date (YYYY-MM-DD).
      "last_N_days" ends yesterday so every day is complete.
    - breakdown: "day" / "week" / "month" for a trend instead of one total.
    - segment_by: split by age, gender, keyword, interest, audience, placement,
      device, region, country or media_type.
    - name_contains: keep rows whose campaign, ad group or ad name contains
      these words.
    - sort_by / ascending / min_spend / limit: e.g. worst ROAS among ads with
      real spend is sort_by="roas", ascending=True, min_spend=100.
    - include_video: adds hook rate (3s views / impressions), hold rate and
      quartile views. Use for creative analysis.
    - *_window_days: attribution windows. Left unset, Pinterest's defaults
      apply (30-day click, 30-day engagement, 1-day view). If the account
      context names windows, pass those.

    ROAS and purchases are Pinterest-attributed. Row ids are Pinterest's own
    and match Northbeam's campaignId / adsetId / adId, so when Northbeam tools
    are connected, pull the same dates there and join on id (see the
    attribution-northbeam playbook).
    """
    start, end = resolve_dates(date_range, start_date, end_date)
    api_level, dims = LEVELS[level]
    columns = dims + METRICS + (VIDEO_METRICS if include_video else [])
    body: dict[str, Any] = {
        "start_date": start,
        "end_date": end,
        "granularity": breakdown.upper(),
        "level": api_level,
        "columns": columns,
    }
    if segment_by != "none":
        body["level"] = api_level + "_TARGETING"
        body["targeting_types"] = [SEGMENTS[segment_by]]
    if campaign_ids:
        body["campaign_ids"] = [str(i) for i in campaign_ids]
    if ad_group_ids:
        body["ad_group_ids"] = [str(i) for i in ad_group_ids]
    windows = {
        "click_window_days": click_window_days,
        "engagement_window_days": engagement_window_days,
        "view_window_days": view_window_days,
    }
    body.update({k: v for k, v in windows.items() if v is not None})

    raw_rows = reports.flatten(reports.run(client(), body))

    rows = []
    totals: dict[str, float] = {}
    for raw in raw_rows:
        names = " ".join(
            str(raw.get(k) or "") for k in ("CAMPAIGN_NAME", "AD_GROUP_NAME", "AD_NAME")
        )
        if not _name_matches(names, name_contains):
            continue
        for key in METRICS + VIDEO_METRICS:
            totals[key] = totals.get(key, 0) + float(raw.get(key) or 0)
        row: dict[str, Any] = {}
        if level == "campaign":
            row.update(
                name=raw.get("CAMPAIGN_NAME"),
                id=str(raw.get("CAMPAIGN_ID")),
                status=raw.get("CAMPAIGN_ENTITY_STATUS"),
                campaign_id=str(raw.get("CAMPAIGN_ID")),
            )
        elif level == "ad_group":
            row.update(
                name=raw.get("AD_GROUP_NAME"),
                id=str(raw.get("AD_GROUP_ID")),
                status=raw.get("AD_GROUP_ENTITY_STATUS"),
                campaign=raw.get("CAMPAIGN_NAME"),
                campaign_id=str(raw.get("CAMPAIGN_ID")),
            )
        elif level == "ad":
            row.update(
                name=raw.get("AD_NAME"),
                id=str(raw.get("AD_ID")),
                pin_id=str(raw.get("PIN_ID")),
                ad_group=raw.get("AD_GROUP_NAME"),
                ad_group_id=str(raw.get("AD_GROUP_ID")),
                campaign=raw.get("CAMPAIGN_NAME"),
                campaign_id=str(raw.get("CAMPAIGN_ID")),
            )
        if segment_by != "none":
            row["segment"] = raw.get("TARGETING_VALUE")
        if breakdown != "total":
            row["date"] = raw.get("DATE")
        row.update(summarize(raw, include_video))
        if row["spend"] >= min_spend:
            rows.append(row)

    if sort_by in ("name", "date"):
        rows.sort(key=lambda r: str(r.get(sort_by) or "").lower(), reverse=not ascending)
    else:
        # Rows where the metric is undefined (e.g. CPA with no purchases) go last.
        known = [r for r in rows if r.get(sort_by) is not None]
        unknown = [r for r in rows if r.get(sort_by) is None]
        known.sort(key=lambda r: r[sort_by], reverse=not ascending)
        rows = known + unknown
    shown = rows[: max(1, min(limit, 500))]

    attribution = {k: v for k, v in windows.items() if v is not None} or (
        "Pinterest defaults: 30-day click, 30-day engagement, 1-day view"
    )
    return _render(
        {
            "level": level,
            "date_range": {"start": start, "end": end},
            "breakdown": breakdown,
            "segment_by": segment_by,
            "attribution": attribution,
            "totals": summarize(totals, include_video),
            "row_count": len(rows),
            "rows_shown": len(shown),
            "rows": shown,
            "definitions": DEFINITIONS,
            "attribution_source": (
                "Pinterest. If Northbeam tools are available in this session, read "
                "pinterest_playbook('attribution-northbeam') and show Northbeam ROAS for the "
                "same dates beside these; row ids match Northbeam's campaignId / adsetId / adId."
            ),
        },
        "performance",
    )


@mcp.tool(annotations=READ)
@_wrap
def pinterest_find(
    name: str = "",
    kind: Literal["campaign", "ad_group", "ad"] = "campaign",
    status: Literal["active", "paused", "archived", "any"] = "active",
    campaign_ids: list[str] | None = None,
    ad_group_ids: list[str] | None = None,
    limit: int = 50,
) -> str:
    """Find campaigns, ad groups (ad sets) or ads by name and get their ids.

    People refer to things by name ("the Best Sellers Video campaign"); the API
    needs ids. Use this before any change. `name` matches when every word
    appears in the name, case-insensitive; leave it empty to list everything
    with that status. Returns id, name, status, daily budget in dollars and
    parent ids. Narrow ads with campaign_ids or ad_group_ids.
    """
    statuses = (
        ["ACTIVE", "PAUSED", "ARCHIVED"] if status == "any" else [status.upper()]
    )
    query: dict[str, Any] = {"entity_statuses": statuses}
    if campaign_ids:
        query["campaign_ids"] = [str(i) for i in campaign_ids]
    if ad_group_ids and kind != "campaign":
        query["ad_group_ids"] = [str(i) for i in ad_group_ids]
    path = {
        "campaign": "/ad_accounts/{ad_account_id}/campaigns",
        "ad_group": "/ad_accounts/{ad_account_id}/ad_groups",
        "ad": "/ad_accounts/{ad_account_id}/ads",
    }[kind]
    items, truncated = _list_all(path, query)

    found = []
    for item in items:
        if not _name_matches(item.get("name", ""), name):
            continue
        row: dict[str, Any] = {
            "id": item.get("id"),
            "name": item.get("name"),
            "status": item.get("status"),
        }
        if kind == "campaign":
            row["daily_budget"] = _usd(item.get("daily_spend_cap"))
            row["lifetime_budget"] = _usd(item.get("lifetime_spend_cap"))
            row["objective"] = item.get("objective_type")
            row["budget_set_at"] = (
                "campaign" if item.get("is_campaign_budget_optimization") else "ad_group"
            )
        elif kind == "ad_group":
            row["campaign_id"] = item.get("campaign_id")
            row["budget"] = _usd(item.get("budget_in_micro_currency"))
            row["budget_type"] = item.get("budget_type")
            row["bid_strategy"] = item.get("bid_strategy_type")
        else:
            row["ad_group_id"] = item.get("ad_group_id")
            row["campaign_id"] = item.get("campaign_id")
            row["pin_id"] = item.get("pin_id")
            row["creative_type"] = item.get("creative_type")
            row["destination_url"] = item.get("destination_url")
            row["review_status"] = item.get("review_status")
        found.append(row)

    out: dict[str, Any] = {
        "kind": kind,
        "status": status,
        "matches": len(found),
        "items": found[: max(1, min(limit, 500))],
    }
    if truncated:
        out["note"] = "The account has more than could be scanned; narrow with campaign_ids."
    return _render(out, "find")


@mcp.tool(annotations=READ)
@_wrap
def pinterest_account_overview(date_range: DateRange = "last_7_days") -> str:
    """One-call snapshot of the ad account: what is live, budgets, and results.

    The tool for "what's running", "give me the lay of the land", or the start
    of any review. Returns the account, every active campaign with its daily
    budget, total daily budget committed, counts of active ad groups, and
    account plus per-campaign performance for the period.
    """
    api = client()
    account = api.request("GET", "/ad_accounts/{ad_account_id}")
    campaigns, _ = _list_all(
        "/ad_accounts/{ad_account_id}/campaigns", {"entity_statuses": ["ACTIVE"]}
    )
    ad_groups, _ = _list_all(
        "/ad_accounts/{ad_account_id}/ad_groups", {"entity_statuses": ["ACTIVE"]}
    )
    active_ids = {c.get("id") for c in campaigns}
    live_groups = [g for g in ad_groups if g.get("campaign_id") in active_ids]
    groups_per_campaign: dict[str, int] = {}
    for group in live_groups:
        groups_per_campaign[group["campaign_id"]] = groups_per_campaign.get(group["campaign_id"], 0) + 1

    start, end = resolve_dates(date_range, None, None)
    raw_rows = reports.flatten(
        reports.run(
            api,
            {
                "start_date": start,
                "end_date": end,
                "granularity": "TOTAL",
                "level": "CAMPAIGN",
                "columns": LEVELS["campaign"][1] + METRICS,
            },
        )
    )
    by_campaign = {str(r.get("CAMPAIGN_ID")): r for r in raw_rows}
    totals: dict[str, float] = {}
    for raw in raw_rows:
        for key in METRICS:
            totals[key] = totals.get(key, 0) + float(raw.get(key) or 0)

    live = []
    for campaign in campaigns:
        perf = summarize(by_campaign.get(str(campaign.get("id")), {}))
        live.append(
            {
                "name": campaign.get("name"),
                "id": campaign.get("id"),
                "objective": campaign.get("objective_type"),
                "daily_budget": _usd(campaign.get("daily_spend_cap")),
                "active_ad_groups": groups_per_campaign.get(campaign.get("id"), 0),
                "spend": perf["spend"],
                "revenue": perf["revenue"],
                "roas": perf["roas"],
                "purchases": perf["purchases"],
                "cpa": perf["cpa"],
            }
        )
    live.sort(key=lambda r: -(r["spend"] or 0))
    spending_but_not_active = [
        {"name": r.get("CAMPAIGN_NAME"), "status": r.get("CAMPAIGN_ENTITY_STATUS"),
         "spend": round(float(r.get("SPEND_IN_DOLLAR") or 0), 2)}
        for cid, r in by_campaign.items()
        if cid not in {str(i) for i in active_ids} and float(r.get("SPEND_IN_DOLLAR") or 0) > 0
    ]
    return _render(
        {
            "account": {k: account.get(k) for k in ("id", "name", "currency", "country")},
            "date_range": {"start": start, "end": end},
            "attribution": "Pinterest defaults: 30-day click, 30-day engagement, 1-day view",
            "active_campaigns": len(campaigns),
            "active_ad_groups": len(live_groups),
            "total_daily_budget": round(
                sum(r["daily_budget"] or 0 for r in live), 2
            ),
            "account_totals": summarize(totals),
            "campaigns": live,
            "spent_in_period_but_no_longer_active": spending_but_not_active,
            "has_saved_context": context_path().exists(),
        },
        "overview",
    )


# ── changing ──────────────────────────────────────────────────────────────────

COLLECTION = {
    "campaign": "/ad_accounts/{ad_account_id}/campaigns",
    "ad_group": "/ad_accounts/{ad_account_id}/ad_groups",
    "ad": "/ad_accounts/{ad_account_id}/ads",
}
ID_FILTER = {"campaign": "campaign_ids", "ad_group": "ad_group_ids", "ad": "ad_ids"}


def _fetch(kind: str, ids: list[str]) -> dict[str, dict]:
    page = client().request(
        "GET",
        COLLECTION[kind],
        query={
            ID_FILTER[kind]: ids,
            "entity_statuses": ["ACTIVE", "PAUSED", "ARCHIVED"],
            "page_size": 250,
        },
    )
    return {str(item.get("id")): item for item in page.get("items") or []}


@mcp.tool(annotations=WRITE)
@_wrap
def pinterest_set_status(
    kind: Literal["campaign", "ad_group", "ad"],
    ids: list[str],
    status: Literal["ACTIVE", "PAUSED", "ARCHIVED"],
) -> str:
    """Pause, resume or archive campaigns, ad groups (ad sets) or ads.

    LIVE change. "Turn off" / "kill" / "stop" means PAUSED; "turn on" /
    "relaunch" means ACTIVE; ARCHIVED removes it from the account views and is
    the closest thing to delete. Get ids from pinterest_find or
    pinterest_performance, and tell the user which names you are about to
    change. Up to 30 ids per call. Returns each entity's status before and after.
    """
    ids = [str(i) for i in ids]
    if not ids or len(ids) > 30:
        raise ValueError("Pass between 1 and 30 ids.")
    before = _fetch(kind, ids)
    missing = [i for i in ids if i not in before]
    if missing:
        raise ValueError(f"No {kind} found with id(s): {', '.join(missing)}")
    result = client().request(
        "PATCH", COLLECTION[kind], body=[{"id": i, "status": status} for i in ids]
    )
    _check_exceptions(result)
    return json.dumps(
        {
            "changed": [
                {
                    "id": i,
                    "name": before[i].get("name"),
                    "from": before[i].get("status"),
                    "to": status,
                }
                for i in ids
            ]
        },
        indent=1,
    )


@mcp.tool(annotations=WRITE)
@_wrap
def pinterest_set_daily_budget(
    kind: Literal["campaign", "ad_group"],
    id: str,
    daily_budget_usd: float,
    allow_large_increase: bool = False,
) -> str:
    """Set the daily budget of a campaign or ad group (ad set), in dollars.

    LIVE change that affects spend. Pass the new daily amount in plain dollars
    (500 means $500 a day). For "scale 20%", read the current budget with
    pinterest_find and pass the computed amount. Budgets live on the campaign
    when it uses campaign budget optimisation, otherwise on its ad groups;
    pinterest_find shows which as `budget_set_at`.

    An increase to more than double the current budget is refused unless
    allow_large_increase is true, to catch typos. Only set that after the user
    has confirmed the number.
    """
    if daily_budget_usd <= 0:
        raise ValueError("daily_budget_usd must be a positive dollar amount.")
    current = _fetch(kind, [str(id)]).get(str(id))
    if not current:
        raise ValueError(f"No {kind} found with id {id}")
    field = "daily_spend_cap" if kind == "campaign" else "budget_in_micro_currency"
    if kind == "campaign" and not current.get("daily_spend_cap"):
        raise ValueError(
            f"Campaign '{current.get('name')}' has no daily budget of its own "
            "(it uses a lifetime budget or ad-group budgets). Set the budget on its ad groups."
        )
    if kind == "ad_group" and (current.get("budget_type") or "DAILY") != "DAILY":
        raise ValueError(
            f"Ad group '{current.get('name')}' uses a {current.get('budget_type')} budget, "
            "not a daily one. Change it with pinterest_api_write if that is intended."
        )
    old = _usd(current.get(field)) or 0
    if old and daily_budget_usd > old * 2 and not allow_large_increase:
        raise ValueError(
            f"That would raise '{current.get('name')}' from ${old:,.2f} to "
            f"${daily_budget_usd:,.2f} a day, more than double. Confirm the amount with "
            "the user, then call again with allow_large_increase=true."
        )
    result = client().request(
        "PATCH", COLLECTION[kind], body=[{"id": str(id), field: int(round(daily_budget_usd * MICRO))}]
    )
    _check_exceptions(result)
    return json.dumps(
        {
            "id": str(id),
            "name": current.get("name"),
            "daily_budget_from": old,
            "daily_budget_to": round(daily_budget_usd, 2),
            "change_pct": _div(daily_budget_usd - old, old, 100, 1),
        },
        indent=1,
    )


# ── prompts ───────────────────────────────────────────────────────────────────


@mcp.prompt(title="Daily account check")
def daily_check() -> str:
    """Yesterday against the last 7 days, with what needs attention."""
    return (
        "Run a daily check on the Pinterest ad account.\n"
        "1. pinterest_account_context for targets and attribution windows, and "
        "pinterest_playbook('growth-marketer-playbook') for how to triage and read trends.\n"
        "2. pinterest_account_overview for what is live and its budgets.\n"
        "3. pinterest_performance at campaign level for yesterday and for last_7_days, "
        "using the attribution windows from the context.\n"
        "   Follow pinterest_playbook('analysis-and-presentation') for rigor and format.\n"
        "4. Report: total spend, revenue, ROAS and CPA yesterday against the 7-day "
        "average and the target; each campaign against target; anything that changed "
        "sharply. Finish with at most three recommended actions, each with the number "
        "behind it. Do not change anything.\n"
        "If Northbeam tools are available, follow pinterest_playbook('attribution-northbeam'): "
        "pull the same dates there and lead with Northbeam ROAS, Pinterest's beside it."
    )


@mcp.prompt(title="Creative report")
def creative_report(date_range: str = "last_14_days", min_spend: str = "100") -> str:
    """Which ads are winning, which are fading, and why."""
    return (
        f"Build a creative report for the Pinterest ad account over {date_range}.\n"
        "1. pinterest_account_context for the target ROAS and attribution windows, and "
        "pinterest_playbook('creative-management') for the lifecycle, pause and decay rules.\n"
        f"2. pinterest_performance with level='ad', include_video=true, min_spend={min_spend}, "
        "sorted by spend.\n"
        "3. Group ads into winners (at or above target ROAS), on the bubble, and losers. "
        "For each, give spend, ROAS, CPA, CTR, hook rate and hold rate.\n"
        "4. Say what the winners have in common and what the losers have in common "
        "(hook, format, angle as far as the names and metrics show), and which losers "
        "have spent enough to turn off. Do not change anything.\n"
        "If Northbeam tools are available, add Northbeam ROAS per ad (level 'ad', join on "
        "adId) and rank on that; see pinterest_playbook('attribution-northbeam')."
    )


@mcp.prompt(title="Budget review")
def budget_review() -> str:
    """Where to move money, with the exact changes proposed."""
    return (
        "Review budgets in the Pinterest ad account.\n"
        "1. pinterest_account_context for targets and any rules on budget changes, and "
        "pinterest_playbook('budget-optimization') and ('growth-marketer-playbook') for "
        "the decision tiers and guards.\n"
        "2. pinterest_account_overview, then pinterest_performance at campaign and "
        "ad_group level for last_7_days and last_3_days.\n"
        "   If Northbeam tools are available, base every move on Northbeam ROAS "
        "(pinterest_playbook('attribution-northbeam')).\n"
        "3. Propose specific moves as a table: entity, current daily budget, proposed "
        "daily budget, percent change, and the ROAS/CPA evidence. Keep any single "
        "increase to 20-30% unless the context says otherwise.\n"
        "4. Stop and ask which changes to apply. Apply only those, with "
        "pinterest_set_daily_budget."
    )


@mcp.prompt(title="Launch ads")
def launch_ads(creative: str = "", ad_group: str = "") -> str:
    """Put new creative live in an existing ad group, paused until approved."""
    return (
        f"Launch new ads in the Pinterest ad account. Creative: {creative or '(ask)'}. "
        f"Destination ad group: {ad_group or '(ask)'}.\n"
        "1. pinterest_account_context for the landing page, tracking parameters, CTA "
        "and naming convention.\n"
        "2. pinterest_find to resolve the ad group. If the creative is an existing ad "
        "or winner, find its ad id too.\n"
        "3. Show the user exactly what you will create: name, creative, destination "
        "URL with tracking parameters, ad group.\n"
        "4. After they approve, call pinterest_launch_ad once per ad (copy_from_ad_id "
        "for an existing ad, video_path or image_path for a new file). Leave status "
        "as PAUSED.\n"
        "5. Report the new ad ids and ask whether to set them ACTIVE."
    )


# ── creatives ─────────────────────────────────────────────────────────────────

SCOPE_NOTE = (
    "The stored token lacks the pins/boards scopes. Run `pinterest-ads-mcp auth` "
    "in the repo to sign in with them."
)
IMAGE_SIZES = ("600x", "400x300", "1200x", "150x150")


def _missing_scope(exc: PinterestError) -> bool:
    return exc.status in (401, 403) and "scope" in json.dumps(exc.payload).lower()


def _pin_summary(pin: dict) -> tuple[dict, str | None]:
    """Reduce a Pin to what a strategist needs, plus the best image URL."""
    media = pin.get("media") or {}
    images = media.get("images") or {}
    image_url = next(
        (images[size]["url"] for size in IMAGE_SIZES if images.get(size, {}).get("url")),
        None,
    ) or media.get("cover_image_url")
    summary = {
        "title": pin.get("title"),
        "description": pin.get("description"),
        "link": pin.get("link"),
        "alt_text": pin.get("alt_text"),
        "media_type": media.get("media_type") or pin.get("creative_type"),
        "duration_seconds": round(media["duration"] / 1000, 1) if media.get("duration") else None,
        "dimensions": f"{media.get('width')}x{media.get('height')}" if media.get("width") else None,
        "image_url": image_url,
        "video_url": media.get("video_url"),
        "board_id": pin.get("board_id"),
        "created_at": pin.get("created_at"),
    }
    return {k: v for k, v in summary.items() if v is not None}, image_url


@mcp.tool(annotations=READ, structured_output=False)
@_wrap
def pinterest_creative(
    ad_id: str | None = None,
    pin_id: str | None = None,
    show_image: bool = True,
) -> list:
    """Look at a creative: what the ad says, where it sends people, and how it looks.

    The tool for "show me that ad", "what's the hook on the winner", "which
    landing page is this ad using". Pass an ad_id (from pinterest_performance or
    pinterest_find) or a pin_id. Returns the ad's name, status, review status
    and destination URL; the Pin's title, description, format and length; a
    preview link that opens the ad as it appears on Pinterest; and, when
    show_image is true, the image or video cover itself so you can describe it.
    """
    if not ad_id and not pin_id:
        raise ValueError("Pass an ad_id or a pin_id.")
    api = client()
    out: dict[str, Any] = {}
    if ad_id:
        ad = _fetch("ad", [str(ad_id)]).get(str(ad_id))
        if not ad:
            raise ValueError(f"No ad found with id {ad_id}")
        pin_id = ad.get("pin_id")
        out["ad"] = {
            "id": ad.get("id"),
            "name": ad.get("name"),
            "status": ad.get("status"),
            "review_status": ad.get("review_status"),
            "rejected_reasons": ad.get("rejected_reasons") or None,
            "creative_type": ad.get("creative_type"),
            "call_to_action": ad.get("customizable_cta_type"),
            "destination_url": ad.get("destination_url"),
            "ad_group_id": ad.get("ad_group_id"),
            "campaign_id": ad.get("campaign_id"),
        }
    out["pin_id"] = str(pin_id)

    # Generates a shareable preview page. It does not change the ad account.
    try:
        preview = api.request(
            "POST", "/ad_accounts/{ad_account_id}/ad_previews", body={"pin_id": str(pin_id)}
        )
        out["preview_url"] = preview.get("url")
    except PinterestError as exc:
        out["preview_url"] = None
        out["preview_note"] = f"No preview available (HTTP {exc.status})."

    image_url = None
    try:
        pin = api.request(
            "GET", f"/pins/{pin_id}", query={"ad_account_id": api.creds.get("ad_account_id")}
        )
        out["pin"], image_url = _pin_summary(pin)
    except PinterestError as exc:
        if not _missing_scope(exc):
            raise
        out["pin"] = None
        out["note"] = (
            "Pin details and the image need the pins:read and boards:read scopes. "
            + SCOPE_NOTE + " Open preview_url to see the creative meanwhile."
        )

    content: list = [json.dumps(out, indent=1, ensure_ascii=False)]
    if show_image and image_url:
        try:
            resp = api.download(image_url)
            kind = resp.headers.get("content-type", "image/jpeg").split("/")[-1].split(";")[0]
            if len(resp.content) <= 4_000_000:
                content.append(Image(data=resp.content, format=kind or "jpeg"))
        except Exception:  # The picture is a bonus; never fail the lookup over it.
            pass
    return content


def _pick_board(board_id: str | None) -> str:
    if board_id:
        return str(board_id)
    api = client()
    boards = api.request(
        "GET", "/boards", query={"ad_account_id": api.creds.get("ad_account_id"), "page_size": 50}
    ).get("items") or []
    if len(boards) == 1:
        return boards[0]["id"]
    if not boards:
        raise ValueError("The account has no boards. Create one on Pinterest first.")
    listing = ", ".join(f"{b.get('name')} ({b.get('id')})" for b in boards[:20])
    raise ValueError(
        f"Pass board_id: new Pins need a board, and there are several. Boards: {listing}"
    )


@mcp.tool(annotations=WRITE)
@_wrap
def pinterest_launch_ad(
    ad_group_id: str,
    copy_from_ad_id: str | None = None,
    pin_id: str | None = None,
    video_path: str | None = None,
    image_path: str | None = None,
    image_url: str | None = None,
    name: str | None = None,
    destination_url: str | None = None,
    title: str | None = None,
    description: str | None = None,
    board_id: str | None = None,
    cover_image_url: str | None = None,
    cover_second: int = 1,
    call_to_action: str | None = None,
    status: Literal["PAUSED", "ACTIVE"] = "PAUSED",
) -> str:
    """Create an ad in an ad group (ad set), from an existing ad or new creative.

    LIVE change. The ad is created PAUSED unless status="ACTIVE", which you
    should pass only when the user said to launch it. Tell the user the ad
    group, name and destination URL before calling.

    Give exactly one creative source:
    - copy_from_ad_id: duplicate an existing ad into this ad group. Its Pin,
      format, destination URL and call to action are reused unless overridden.
      The usual way to move a winner into another ad set.
    - pin_id: promote an existing Pin. Needs destination_url.
    - video_path / image_path: a local file. It is uploaded, turned into a Pin
      and promoted. Needs destination_url and title.
    - image_url: a hosted image. Needs destination_url and title.

    destination_url should be the full landing page including tracking
    parameters; take the account's standard from pinterest_account_context.
    New Pins go on board_id (optional when the account has a single board).
    For video, the cover is cover_image_url or the frame at cover_second.
    """
    sources = [s for s in (copy_from_ad_id, pin_id, video_path, image_path, image_url) if s]
    if len(sources) != 1:
        raise ValueError(
            "Give exactly one of copy_from_ad_id, pin_id, video_path, image_path, image_url."
        )
    api = client()
    group = _fetch("ad_group", [str(ad_group_id)]).get(str(ad_group_id))
    if not group:
        raise ValueError(f"No ad group found with id {ad_group_id}")

    creative_type = "REGULAR"
    created_pin = False
    if copy_from_ad_id:
        source = _fetch("ad", [str(copy_from_ad_id)]).get(str(copy_from_ad_id))
        if not source:
            raise ValueError(f"No ad found with id {copy_from_ad_id}")
        pin_id = source.get("pin_id")
        creative_type = source.get("creative_type") or "REGULAR"
        name = name or source.get("name")
        destination_url = destination_url or source.get("destination_url")
        call_to_action = call_to_action or source.get("customizable_cta_type")
    elif not pin_id:
        if not destination_url or not title:
            raise ValueError("New creative needs destination_url and title.")
        local = Path(video_path or image_path).expanduser() if (video_path or image_path) else None
        if local and not local.is_file():
            raise ValueError(f"File not found: {local}")
        try:
            board = _pick_board(board_id)
            if video_path:
                creative_type = "VIDEO"
                media = upload_video(api, local)
                if media["status"] != "succeeded":
                    raise ValueError(
                        f"Video {media['media_id']} is still processing on Pinterest. "
                        "Try again in a minute."
                    )
                media_source: dict[str, Any] = {
                    "source_type": "video_id",
                    "media_id": media["media_id"],
                }
                if cover_image_url:
                    media_source["cover_image_url"] = cover_image_url
                else:
                    media_source["cover_image_key_frame_time"] = max(0, cover_second)
            elif image_path:
                suffix = local.suffix.lower()
                if suffix not in (".jpg", ".jpeg", ".png"):
                    raise ValueError("image_path must be a .jpg or .png file.")
                media_source = {
                    "source_type": "image_base64",
                    "content_type": "image/png" if suffix == ".png" else "image/jpeg",
                    "data": expand_files(f"@base64:{local}"),
                }
            else:
                media_source = {"source_type": "image_url", "url": image_url}
            pin_body = {
                "board_id": board,
                "title": title,
                "description": description or "",
                "link": destination_url,
                "media_source": media_source,
            }
            pin = api.request(
                "POST", "/pins", query={"ad_account_id": api.creds.get("ad_account_id")},
                body=pin_body,
            )
        except PinterestError as exc:
            if _missing_scope(exc):
                raise ValueError(
                    "Creating a Pin from new creative needs the pins:write and "
                    "boards:read scopes. " + SCOPE_NOTE
                ) from None
            raise
        pin_id = pin["id"]
        created_pin = True

    if not destination_url:
        raise ValueError("destination_url is required when promoting a Pin by pin_id.")
    ad_body: dict[str, Any] = {
        "ad_group_id": str(ad_group_id),
        "pin_id": str(pin_id),
        "creative_type": creative_type,
        "destination_url": destination_url,
        "status": status,
    }
    if name:
        ad_body["name"] = name
    if call_to_action:
        ad_body["customizable_cta_type"] = call_to_action
    try:
        result = api.request("POST", COLLECTION["ad"], body=[ad_body])
        _check_exceptions(result)
    except (PinterestError, ValueError) as exc:
        if created_pin:
            raise ValueError(
                f"The Pin was created (pin_id {pin_id}) but the ad was not: {exc} "
                "Retry with pin_id instead of the file."
            ) from None
        raise
    created = ((result.get("items") or [{}])[0].get("data")) or {}
    return json.dumps(
        {
            "ad_id": created.get("id"),
            "name": created.get("name") or name,
            "status": created.get("status") or status,
            "review_status": created.get("review_status"),
            "pin_id": str(pin_id),
            "new_pin_created": created_pin,
            "creative_type": creative_type,
            "ad_group": {"id": str(ad_group_id), "name": group.get("name")},
            "destination_url": destination_url,
            "next": "Pinterest reviews new ads before they serve."
            + ("" if status == "ACTIVE" else " It is PAUSED: set it ACTIVE with pinterest_set_status."),
        },
        indent=1,
    )


# ── analysis: confidence, comparison, dashboard ───────────────────────────────

LOW_DATA_PURCHASES = 10
SOLID_PURCHASES = 30


def confidence(purchases: float) -> str:
    """How much a row's ROAS and CPA can be trusted, by purchase count."""
    if purchases < LOW_DATA_PURCHASES:
        return "low"
    return "solid" if purchases >= SOLID_PURCHASES else "directional"


def two_proportion(success_a: float, n_a: float, success_b: float, n_b: float) -> dict | None:
    """Two-sided z-test for a difference between two rates, with a 95% interval."""
    if n_a <= 0 or n_b <= 0:
        return None
    p_a, p_b = success_a / n_a, success_b / n_b
    pooled = (success_a + success_b) / (n_a + n_b)
    se_pooled = math.sqrt(pooled * (1 - pooled) * (1 / n_a + 1 / n_b))
    se = math.sqrt(p_a * (1 - p_a) / n_a + p_b * (1 - p_b) / n_b)
    z = (p_a - p_b) / se_pooled if se_pooled else 0.0
    p_value = math.erfc(abs(z) / math.sqrt(2))
    diff = p_a - p_b
    return {
        "a_rate_pct": round(p_a * 100, 3),
        "b_rate_pct": round(p_b * 100, 3),
        "relative_lift_pct": round((p_a / p_b - 1) * 100, 1) if p_b else None,
        "difference_pct_points": round(diff * 100, 3),
        "ci95_pct_points": [round((diff - 1.96 * se) * 100, 3), round((diff + 1.96 * se) * 100, 3)],
        "p_value": round(p_value, 4),
        "significant_at_95": p_value < 0.05,
        "enough_data": min(success_a, success_b) >= 5,
    }


@mcp.tool(annotations=READ)
@_wrap
def pinterest_compare(
    level: Literal["campaign", "ad_group", "ad"],
    id_a: str,
    id_b: str,
    date_range: DateRange = "last_14_days",
    start_date: str | None = None,
    end_date: str | None = None,
    click_window_days: Literal[0, 1, 7, 14, 30, 60] | None = None,
    engagement_window_days: Literal[0, 1, 7, 14, 30, 60] | None = None,
    view_window_days: Literal[0, 1, 7, 14, 30, 60] | None = None,
) -> str:
    """Is A really beating B, or is it noise? Statistical comparison of two entities.

    Use before calling a winner between two ads, ad groups or campaigns. Runs
    two-proportion tests on click-through rate (clicks / impressions) and
    conversion rate (purchases / clicks), with p-values and 95% intervals, and
    reports ROAS and CPA side by side. Returns a plain verdict and says when
    there is not enough data to conclude anything.
    """
    start, end = resolve_dates(date_range, start_date, end_date)
    api_level, dims = LEVELS[level]
    body: dict[str, Any] = {
        "start_date": start, "end_date": end, "granularity": "TOTAL",
        "level": api_level, "columns": dims + METRICS,
    }
    windows = {"click_window_days": click_window_days,
               "engagement_window_days": engagement_window_days,
               "view_window_days": view_window_days}
    body.update({k: v for k, v in windows.items() if v is not None})
    id_column = {"campaign": "CAMPAIGN_ID", "ad_group": "AD_GROUP_ID", "ad": "AD_ID"}[level]
    name_column = {"campaign": "CAMPAIGN_NAME", "ad_group": "AD_GROUP_NAME", "ad": "AD_NAME"}[level]
    rows = {str(r.get(id_column)): r for r in reports.flatten(reports.run(client(), body))}
    missing = [i for i in (str(id_a), str(id_b)) if i not in rows]
    if missing:
        raise ValueError(f"No delivery in {start} to {end} for {level} id(s): {', '.join(missing)}")
    a, b = rows[str(id_a)], rows[str(id_b)]

    def side(raw: dict) -> dict:
        s = summarize(raw)
        return {"name": raw.get(name_column), **{k: s[k] for k in (
            "spend", "impressions", "clicks", "purchases", "revenue", "roas", "cpa", "ctr", "cvr")},
            "confidence": confidence(s["purchases"])}

    sa, sb = side(a), side(b)
    ctr = two_proportion(sa["clicks"], sa["impressions"], sb["clicks"], sb["impressions"])
    cvr = two_proportion(sa["purchases"], sa["clicks"], sb["purchases"], sb["clicks"])

    def read(test: dict | None, what: str) -> str:
        if not test:
            return f"{what}: no data."
        if not test["enough_data"]:
            return f"{what}: too few events to test."
        if not test["significant_at_95"]:
            return f"{what}: no reliable difference (p={test['p_value']})."
        winner = "A" if test["difference_pct_points"] > 0 else "B"
        return f"{what}: {winner} is higher, and it is unlikely to be chance (p={test['p_value']})."

    verdict = [read(ctr, "Click-through rate"), read(cvr, "Conversion rate")]
    if "low" in (sa["confidence"], sb["confidence"]):
        verdict.append(
            f"At least one side has under {LOW_DATA_PURCHASES} purchases, so its ROAS and CPA "
            "are not stable yet. Do not pick a winner on ROAS."
        )
    return json.dumps(
        {
            "date_range": {"start": start, "end": end},
            "a": {"id": str(id_a), **sa},
            "b": {"id": str(id_b), **sb},
            "click_through_rate_test": ctr,
            "conversion_rate_test": cvr,
            "verdict": verdict,
            "note": "ROAS and CPA are shown, not tested: revenue per order varies too much "
                    "for a simple test. Trust them only when both sides are 'solid'.",
        },
        indent=1,
    )


def _daily(raw_rows: list[dict], start: date, end: date) -> list[dict]:
    """One summarized row per calendar day, including days with no delivery."""
    by_day = {str(r.get("DATE"))[:10]: r for r in raw_rows}
    out = []
    current = start
    while current <= end:
        key = current.isoformat()
        out.append({"date": key, **summarize(by_day.get(key, {}))})
        current += timedelta(days=1)
    return out


def _sum(rows: list[dict]) -> dict:
    totals: dict[str, float] = {}
    for raw in rows:
        for key in METRICS + VIDEO_METRICS:
            totals[key] = totals.get(key, 0) + float(raw.get(key) or 0)
    return totals


@mcp.tool(annotations=READ)
@_wrap
def pinterest_dashboard(
    date_range: DateRange = "last_14_days",
    start_date: str | None = None,
    end_date: str | None = None,
    target_roas: float | None = None,
    headline: str | None = None,
    insights: list[str] | None = None,
    northbeam_campaigns: list[dict[str, Any]] | None = None,
    click_window_days: Literal[0, 1, 7, 14, 30, 60] | None = None,
    engagement_window_days: Literal[0, 1, 7, 14, 30, 60] | None = None,
    view_window_days: Literal[0, 1, 7, 14, 30, 60] | None = None,
    open_in_browser: bool = True,
    output_dir: str | None = None,
) -> str:
    """Build a visual performance report: one polished, interactive HTML page.

    The tool for "make me a report", "show me a dashboard", "visualise this",
    "something I can send my boss". The page has the headline ROAS against
    target, KPI tiles with change versus the prior period and sparklines, daily
    spend and daily ROAS charts with hover detail, sortable campaign and
    creative tables with on-target / below / low-data status, spend by age, and
    a daily table. It is a single self-contained file: light and dark mode, no
    internet needed, safe to share or publish. It opens in the browser unless
    open_in_browser is false. Read-only for the ad account.

    Make it yours before calling:
    - target_roas: from pinterest_account_context, so status is judged against it.
    - headline + insights: your own analysis, one sentence and 3-5 short
      bullets, written after looking at pinterest_performance. They appear at
      the top as "what matters". Lead with the conclusion, give the number.
    - northbeam_campaigns: when Northbeam is connected, pass its rows as
      [{"campaign_id": "...", "spend": 0.0, "revenue": 0.0}] for the same dates.
      The headline becomes Northbeam ROAS and the campaign table shows both.
    - *_window_days: the account's attribution windows.

    Returns the file path and the numbers behind the page.
    """
    start_s, end_s = resolve_dates(date_range, start_date, end_date)
    start, end = date.fromisoformat(start_s), date.fromisoformat(end_s)
    days = (end - start).days + 1
    prior_start = start - timedelta(days=days)
    windows = {"click_window_days": click_window_days,
               "engagement_window_days": engagement_window_days,
               "view_window_days": view_window_days}
    extra = {k: v for k, v in windows.items() if v is not None}
    api = client()

    def report(level: str, columns: list[str], first: date, granularity: str, **more) -> list[dict]:
        return reports.flatten(reports.run(api, {
            "start_date": first.isoformat(), "end_date": end_s, "granularity": granularity,
            "level": level, "columns": columns, **extra, **more}))

    jobs = {
        "daily": lambda: report("ADVERTISER", METRICS, prior_start, "DAY"),
        "campaigns": lambda: report("CAMPAIGN", LEVELS["campaign"][1] + METRICS, start, "TOTAL"),
        "ads": lambda: report("PIN_PROMOTION", LEVELS["ad"][1] + METRICS + VIDEO_METRICS, start, "TOTAL"),
        "age": lambda: report("ADVERTISER_TARGETING", METRICS, start, "TOTAL", targeting_types=["AGE_BUCKET"]),
    }
    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = {name: pool.submit(job) for name, job in jobs.items()}
        raw = {name: future.result() for name, future in futures.items()}
    account = api.request("GET", "/ad_accounts/{ad_account_id}")

    current_days = [r for r in raw["daily"] if str(r.get("DATE"))[:10] >= start_s]
    prior_days = [r for r in raw["daily"] if str(r.get("DATE"))[:10] < start_s]
    totals = summarize(_sum(current_days))
    previous = summarize(_sum(prior_days)) if prior_days else None

    campaigns = []
    nb = {str(r.get("campaign_id")): r for r in (northbeam_campaigns or []) if r.get("campaign_id")}
    for r in raw["campaigns"]:
        row = {"name": r.get("CAMPAIGN_NAME"), "id": str(r.get("CAMPAIGN_ID")), **summarize(r)}
        match = nb.get(row["id"])
        if match and float(match.get("spend") or 0) > 0:
            row["nb_roas"] = round(float(match.get("revenue") or 0) / float(match["spend"]), 2)
        campaigns.append(row)
    campaigns.sort(key=lambda r: -r["spend"])

    min_ad_spend = max(50, round(totals["spend"] * 0.01 / 50) * 50)
    ads = [
        {"name": r.get("AD_NAME"), "id": str(r.get("AD_ID")), "campaign": r.get("CAMPAIGN_NAME"),
         **summarize(r, video=True)}
        for r in raw["ads"]
    ]
    ads = sorted((a for a in ads if a["spend"] >= min_ad_spend), key=lambda a: -a["spend"])[:40]
    age = [{"segment": r.get("TARGETING_VALUE"), **{k: v for k, v in summarize(r).items()
            if k in ("spend", "roas", "purchases")}} for r in raw["age"] if r.get("TARGETING_VALUE")]

    attribution = (
        ", ".join(f"{v}-day {k.split('_')[0]}" for k, v in extra.items())
        if extra else "30-day click, 30-day engagement, 1-day view"
    )
    hero_roas, hero_label = totals["roas"], "ROAS · Pinterest-attributed"
    if nb:
        nb_spend = sum(float(r.get("spend") or 0) for r in nb.values())
        nb_revenue = sum(float(r.get("revenue") or 0) for r in nb.values())
        if nb_spend > 0:
            hero_roas, hero_label = round(nb_revenue / nb_spend, 2), "ROAS · Northbeam"

    page = dashboard.render({
        "account": account.get("name") or "Pinterest",
        "start": start_s, "end": end_s,
        "attribution": f"Pinterest attribution: {attribution}",
        "source_note": "Delivery and Pinterest-attributed results from the Pinterest API"
                       + (", Northbeam ROAS supplied from Northbeam." if nb else "."),
        "target_roas": target_roas,
        "hero_roas": hero_roas, "hero_label": hero_label,
        "roas_label": "Pinterest-attributed ROAS",
        "totals": totals, "previous": previous,
        "daily": _daily(current_days, start, end),
        "campaigns": campaigns, "ads": ads, "age": age,
        "has_northbeam": bool(nb), "min_ad_spend": min_ad_spend,
        "low_data_purchases": LOW_DATA_PURCHASES,
        "headline": headline, "insights": insights or [],
    })

    folder = Path(output_dir or "~/Documents/Pinterest Ads Reports").expanduser()
    folder.mkdir(parents=True, exist_ok=True)
    slug = "".join(c if c.isalnum() else "-" for c in (account.get("name") or "pinterest").lower()).strip("-")
    path = folder / f"{slug}-{start_s}-to-{end_s}.html"
    path.write_text(page, encoding="utf-8")
    opened = False
    if open_in_browser:
        try:
            opened = webbrowser.open(path.as_uri())
        except Exception:
            opened = False
    return json.dumps(
        {
            "file": str(path),
            "opened_in_browser": bool(opened),
            "date_range": {"start": start_s, "end": end_s},
            "prior_period": {"start": prior_start.isoformat(), "end": (start - timedelta(days=1)).isoformat()},
            "attribution": attribution,
            "totals": totals,
            "previous_totals": previous,
            "campaigns": len(campaigns), "ads_shown": len(ads), "min_ad_spend": min_ad_spend,
            "next": "Tell the user where the file is. It is one self-contained HTML file: they can "
                    "open, attach or share it, and if you have a tool that publishes HTML as an "
                    "artifact or page you may offer to publish it.",
        },
        indent=1,
    )


@mcp.prompt(title="Performance report")
def performance_report(date_range: str = "last_14_days") -> str:
    """A polished visual report with the analysis written in."""
    return (
        f"Build a Pinterest performance report for {date_range}.\n"
        "1. pinterest_account_context for the target and attribution windows; "
        "pinterest_playbook('analysis-and-presentation') for how to analyse and present.\n"
        "2. pinterest_performance at campaign and ad level for the period and the prior "
        "period. Decompose any ROAS change into CPM, CTR, CVR and AOV, and find where it "
        "lives. If Northbeam tools are available, pull the same campaigns there "
        "(pinterest_playbook('attribution-northbeam')).\n"
        "3. Write one headline sentence and three to five insights, each with its number.\n"
        "4. Call pinterest_dashboard with target_roas, the attribution windows, your "
        "headline and insights, and northbeam_campaigns if you have them.\n"
        "5. Tell the user where the report is, repeat the headline and the top "
        "recommendation, and ask what to act on. Do not change anything."
    )
