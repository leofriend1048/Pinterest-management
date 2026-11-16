"""MCP tools. Seven generic tools cover every Pinterest v5 endpoint."""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from typing import Any, Literal

from mcp.server.fastmcp import FastMCP
from mcp.types import ToolAnnotations

from . import credentials, reports, spec as spec_module
from .client import Client, PinterestError, expand_files, upload_video

INSTRUCTIONS = """\
Pinterest Ads for media buyers, growth marketers and creative strategists.
Nothing here runs on its own: every call is made because the user asked.

Start a session with pinterest_account_context: it holds this account's target
ROAS/CPA, attribution windows, naming conventions and rules. Judge performance
against those targets, not in the abstract.

Pick the tool by what was asked
- "How are we doing / what's my ROAS / which ads are winning / what should I
  cut / how did yesterday go"      -> pinterest_performance
- "What's running / what's live"   -> pinterest_account_overview
- A campaign, ad set or ad by name -> pinterest_find (names to ids)
- "Pause / turn off / kill / turn on / archive" -> pinterest_set_status
- "Raise / lower / scale the budget"            -> pinterest_set_daily_budget
- "Make me a report / dashboard / chart / something to send" -> pinterest_dashboard
- "Is A really better than B?"                  -> pinterest_compare
- "Show me that ad / what does the creative look like" -> pinterest_creative
- "Launch this video / put that winner in ad set X / duplicate the ad"
                                                -> pinterest_launch_ad
- "Should I scale / cut / pause this? Is it fatigued? Why did ROAS drop? What
  should we test?"                 -> pinterest_playbook first, then the numbers
- Anything else (build campaigns, audiences, keywords, targeting, pins,
  catalogs, billing): pinterest_search_endpoints, pinterest_describe_endpoint,
  then pinterest_api_read or pinterest_api_write.

How buyers talk, and what it means here
- "Ad set" = ad group. "Creative" = an ad and the Pin behind it.
- "CPA" = spend / purchases (checkouts). "ROAS" = checkout value / spend.
- "Hook rate" = 3-second video views / impressions. "Hold rate" = 95% views /
  3-second views.
- "Scale 20%" = current daily budget x 1.2. "Last 7 days" ends yesterday.
- Numbers from this server are Pinterest-attributed. Say so, and state the
  attribution windows used.

Northbeam
If Northbeam tools (list_dashboards, query_performance) are available in this
session, use them together with this server without being asked: Northbeam for
ROAS, revenue and CPA (the number to decide on), this server for delivery,
creative metrics and every change. Read pinterest_playbook('attribution-northbeam')
for the exact query; the short version is to filter 'breakdown:Platform
(Northbeam)' to 'Pinterest', add campaignId / adsetId / adId, and join on id,
which equals the id in pinterest_performance rows. Show both ROAS figures side
by side, labelled, Northbeam first. If Northbeam is not connected, carry on with
Pinterest's numbers and do not ask the user to install anything. The same
approach applies to any other attribution connector the account context names.

How to work
Be the best media buyer and data scientist the user has worked with: rigorous,
direct and specific. Read pinterest_playbook('analysis-and-presentation') before
a review, report, comparison or visual. In short:
- Lead with the verdict and the number. Compare with target and the prior
  period. Name the campaign or ad. Say plainly when the account is losing money.
- Decompose before concluding: ROAS moves through CPM, CTR, CVR or AOV; find
  which, then go one level down (campaign, ad, segment) to where it lives.
- Respect `confidence` on every row: low (<10 purchases) supports no ROAS or CPA
  claim. Use pinterest_compare before calling a winner between two things.
- The most recent 1-3 days are incomplete because conversions lag.
- Use a clean table for three or more rows: $ for money, x for ROAS, % for
  rates, sorted, right-aligned.
- For a report, dashboard, recap, chart or anything to share, call
  pinterest_dashboard, passing your own headline and insights. It returns a
  polished interactive page. For a narrower custom visual, build an artifact
  following the playbook's chart rules.
- End with a specific recommendation and ask before changing anything.

Raw API rules (generic tools only)
- "{ad_account_id}" in a path is filled in with the default ad account.
- Money is micro-currency: $1 = 1000000.
- Campaign, ad group and ad create/update take a JSON ARRAY; updates go to the
  collection path with "id" in each item.
- Lists page with "bookmark". Status values: ACTIVE, PAUSED, ARCHIVED.

Changes hit the live account and can spend money. Before any change, name the
entities and say what will change. Never change more than was asked. Create
campaigns, ad groups and ads as PAUSED unless the user said to launch them.
"""

mcp = FastMCP("pinterest-ads", instructions=INSTRUCTIONS)

# httpx logs every request URL at INFO; keep the server's stderr quiet.
logging.getLogger("httpx").setLevel(logging.WARNING)

MAX_INLINE_CHARS = 60_000
PREVIEW_CHARS = 12_000
CACHE_DIR = Path("~/.cache/pinterest-ads-mcp").expanduser()

READ = ToolAnnotations(readOnlyHint=True, openWorldHint=True)
WRITE = ToolAnnotations(readOnlyHint=False, destructiveHint=True, openWorldHint=True)
LOCAL = ToolAnnotations(readOnlyHint=True, openWorldHint=False)

_client: Client | None = None


def client() -> Client:
    global _client
    if _client is None:
        _client = Client(spec_module.load())
    return _client


def _as_json(value: Any, name: str) -> Any:
    """Models sometimes send a JSON string where an object is expected."""
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        try:
            return json.loads(text)
        except ValueError as exc:
            raise ValueError(f"`{name}` must be a JSON object/array: {exc}") from exc
    return value


def _render(result: Any, label: str = "response") -> str:
    """Return JSON inline, or a preview plus a file path when it is large."""
    text = json.dumps(result, indent=1, ensure_ascii=False)
    if len(text) <= MAX_INLINE_CHARS:
        return text
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    out = CACHE_DIR / f"{label}-{int(time.time() * 1000)}.json"
    out.write_text(text)
    summary: dict[str, Any] = {
        "note": (
            "Response was too large to return inline. The full JSON is saved at "
            "`saved_to`; read or filter that file (e.g. with jq) for the rest."
        ),
        "saved_to": str(out),
        "total_chars": len(text),
    }
    if isinstance(result, dict):
        summary["top_level_keys"] = list(result.keys())[:50]
        if isinstance(result.get("items"), list):
            summary["item_count"] = len(result["items"])
        if result.get("bookmark"):
            summary["bookmark"] = result["bookmark"]
    elif isinstance(result, list):
        summary["row_count"] = len(result)
    summary["preview"] = text[:PREVIEW_CHARS]
    return json.dumps(summary, indent=1, ensure_ascii=False)


def _call(method: str, path: str, query: Any, body: Any, label: str) -> str:
    try:
        result = client().request(
            method, path, _as_json(query, "query"), expand_files(_as_json(body, "body"))
        )
    except PinterestError as exc:
        raise ValueError(str(exc)) from None
    return _render(result, label)


# ── discovery ─────────────────────────────────────────────────────────────────


@mcp.tool(annotations=LOCAL)
def pinterest_search_endpoints(
    query: str = "",
    tag: str | None = None,
    access: Literal["any", "read", "write"] = "any",
    limit: int = 25,
) -> str:
    """Search the Pinterest v5 API for endpoints.

    Ranks operations by how many words of `query` appear in their ids, paths,
    summaries and descriptions. `tag` narrows to one API area, e.g.
    "campaigns", "ad_groups", "ads", "audiences", "keywords", "pins", "boards",
    "catalogs", "billing". Call with no arguments to list the API areas.
    """
    spec = spec_module.load()
    if not query and not tag:
        return json.dumps(
            {
                "api_version": spec.version,
                "operations": len(spec.operations),
                "tags": spec.tags(),
                "hint": "Search with `query` and/or `tag` to list operations.",
            },
            indent=1,
        )
    found = spec.search(query, tag, access, max(1, min(limit, 100)))
    return json.dumps(
        {"matches": len(found), "operations": [op.brief() for op in found]}, indent=1
    )


@mcp.tool(annotations=LOCAL)
def pinterest_describe_endpoint(operation_id: str, depth: int = 6) -> str:
    """Show one endpoint's parameters, request body schema and response shape.

    `operation_id` comes from pinterest_search_endpoints (e.g. "campaigns/create").
    You can also pass "METHOD /path", e.g. "PATCH /ad_accounts/{ad_account_id}/ads".
    Lower `depth` if a schema is too long; raise it if nested parts are cut off.
    """
    spec = spec_module.load()
    op = spec.get(operation_id)
    if op is None and " " in operation_id.strip():
        method, _, path = operation_id.strip().partition(" ")
        op = spec.match(method, path.strip())
    if op is None:
        near = spec.search(operation_id.replace("/", " "), limit=8)
        raise ValueError(
            f"No operation '{operation_id}'. Closest: "
            + ", ".join(o.id for o in near)
            if near
            else f"No operation '{operation_id}'. Use pinterest_search_endpoints."
        )
    return _render(spec.describe(op, max(0, min(depth, 12))), "schema")


# ── calls ─────────────────────────────────────────────────────────────────────


@mcp.tool(annotations=READ)
def pinterest_api_read(
    path: str,
    query: dict[str, Any] | str | None = None,
    body: dict[str, Any] | list[Any] | str | None = None,
    method: Literal["GET", "POST"] = "GET",
) -> str:
    """Call a Pinterest endpoint that only reads data. Never changes the account.

    `path` is relative to https://api.pinterest.com/v5, e.g.
    "/ad_accounts/{ad_account_id}/campaigns" ({ad_account_id} is filled in).
    `query` holds query-string parameters; list values are encoded correctly.

    `method` is GET except for the few POST endpoints that only compute or
    export something (audience sizing, bid floors, delivery estimates, report
    creation, catalog item lookups); pinterest_describe_endpoint says which.
    Anything else is refused here: use pinterest_api_write.
    """
    spec = spec_module.load()
    if method != "GET":
        try:
            op = spec.match(method, client().normalize_path(path))
        except ValueError:
            op = None
        if op is None or not op.read_only:
            raise ValueError(
                f"{method} {path} is not a read-only operation. "
                "Use pinterest_api_write for calls that change data."
            )
    return _call(method, path, query, body, "read")


@mcp.tool(annotations=WRITE)
def pinterest_api_write(
    method: Literal["POST", "PATCH", "PUT", "DELETE"],
    path: str,
    body: dict[str, Any] | list[Any] | str | None = None,
    query: dict[str, Any] | str | None = None,
) -> str:
    """Call a Pinterest endpoint that creates, edits, pauses or deletes something.

    This acts on the LIVE account: it can launch ads, change budgets and spend
    money. Use it only for a change the user asked for, and check the body
    against pinterest_describe_endpoint first.

    `path` is relative to https://api.pinterest.com/v5 ({ad_account_id} is filled
    in). Campaign, ad group and ad create/update endpoints take a JSON array.
    Amounts are micro-currency ($1 = 1000000).

    To attach a local file as base64 (e.g. media_source.data when creating an
    image Pin), set that field to "@base64:/absolute/path/to/file".
    """
    return _call(method, path, query, body, "write")


@mcp.tool(annotations=READ)
def pinterest_run_report(
    report: dict[str, Any] | str,
    wait_seconds: int = 180,
) -> str:
    """Run an async ads analytics report and return its rows. Read-only.

    Best way to get performance data: any date range, any level, many entities.
    `report` is the body of analytics/create_report, for example:

        {"start_date": "2026-11-01", "end_date": "2026-11-15",
         "granularity": "DAY",            # TOTAL | DAY | HOUR | WEEK | MONTH
         "level": "CAMPAIGN",             # ADVERTISER | CAMPAIGN | AD_GROUP | PIN_PROMOTION | KEYWORD | ...
         "columns": ["CAMPAIGN_NAME", "SPEND_IN_MICRO_DOLLAR", "IMPRESSION_1",
                     "CLICKTHROUGH_1", "TOTAL_CHECKOUT", "TOTAL_CHECKOUT_VALUE_IN_MICRO_DOLLAR"],
         "click_window_days": 7, "view_window_days": 1}

    See pinterest_describe_endpoint("analytics/create_report") for every column,
    filter and attribution option. Spend and value columns are micro-dollars.
    """
    body = dict(_as_json(report, "report") or {})
    try:
        rows = reports.run(client(), body, wait_seconds)
    except reports.ReportPending as pending:
        return json.dumps(
            {
                "note": "Report is still running. Poll it with pinterest_api_read "
                f"GET {reports.PATH} and query {{\"token\": ...}}, then fetch its `url`.",
                "token": pending.token,
                "report_status": pending.state,
            },
            indent=1,
        )
    except PinterestError as exc:
        raise ValueError(str(exc)) from None
    return _render(rows, "report")


@mcp.tool(annotations=WRITE)
def pinterest_upload_video(file_path: str, wait_seconds: int = 300) -> str:
    """Upload a local video file to Pinterest and return its media_id.

    Registers the upload, sends the file, and waits for processing. Use the
    media_id as media_source {"source_type": "video_id", "media_id": ...,
    "cover_image_url": ...} when creating a Pin, then promote that Pin as an ad.
    Needs the pins:write scope. Images are not uploaded this way: create the
    Pin directly with an image_url or "@base64:/path" media source.
    """
    source = Path(file_path).expanduser()
    if not source.is_file():
        raise ValueError(f"File not found: {source}")
    try:
        state = upload_video(client(), source, wait_seconds)
    except PinterestError as exc:
        raise ValueError(str(exc)) from None
    media_id = state["media_id"]
    return json.dumps(
        {
            "media_id": media_id,
            "status": state["status"],
            "note": None
            if state["status"] == "succeeded"
            else f"Still processing. Check with pinterest_api_read GET /media/{media_id}.",
        },
        indent=1,
    )


@mcp.tool(annotations=READ)
def pinterest_auth_status() -> str:
    """Check the Pinterest connection: token, scopes, expiry, default ad account."""
    creds = credentials.load()
    token = creds.get("access_token", "")
    info: dict[str, Any] = {
        "credentials_file": str(credentials.path()),
        "token_present": bool(token),
        "token_source": "environment" if creds.get("from_env") else "credentials file",
        "scope": creds.get("scope") or "unknown (token was not issued by `auth`)",
        "default_ad_account_id": creds.get("ad_account_id"),
        "auto_refresh": bool(
            creds.get("refresh_token") and creds.get("app_id") and creds.get("app_secret")
        ),
        "api_spec_version": spec_module.load().version,
    }
    if creds.get("expires_at"):
        info["expires_in_days"] = round((creds["expires_at"] - time.time()) / 86400, 1)
    if not token:
        info["problem"] = "No token. Run `pinterest-ads-mcp auth` in the repo."
        return json.dumps(info, indent=1)
    try:
        if creds.get("ad_account_id"):
            account = client().request("GET", "/ad_accounts/{ad_account_id}")
            info["ad_account"] = {
                k: account.get(k) for k in ("id", "name", "currency", "country")
            }
            info["ads_access"] = "ok"
        else:
            listing = client().request("GET", "/ad_accounts", query={"page_size": 25})
            info["ad_accounts_visible"] = [
                {"id": a.get("id"), "name": a.get("name")} for a in listing.get("items", [])
            ]
            info["problem"] = "No default ad account set. Add ad_account_id to the credentials file."
    except PinterestError as exc:
        info["ads_access"] = "failed"
        info["problem"] = str(exc)
    return json.dumps(info, indent=1)


# Registers the media-buyer tools and prompts on `mcp`. Imported last because
# buyer.py uses the helpers defined above.
from . import buyer, playbooks  # noqa: E402,F401

mcp.tool(annotations=LOCAL)(playbooks.pinterest_playbook)
mcp._mcp_server.instructions = INSTRUCTIONS + (
    "\nPlaybooks (read with pinterest_playbook before recommending or making a "
    "decision in that area)\n" + playbooks.index() + "\n"
)


def run() -> None:
    mcp.run()
