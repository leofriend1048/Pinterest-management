"""
Northbeam Data Export API connector.

Responsibilities
----------------
- Fetch Pinterest-attributed ROAS, spend, and revenue per campaign and ad
  for the budget engine and ad pause auditor.
- Fetch Meta-attributed creative scoring data for the weekly discovery launcher.

API surface (Northbeam v1 Data Export)
---------------------------------------
  POST /v1/exports/data-export       — submit async export job
  GET  /v1/exports/data-export/result/{id}  — poll for completion
  GET  /v1/exports/attribution-models — discover account model IDs (called once)

Auth
----
  Authorization: Basic <base64(api_key:)>  (api_key as username, empty password)
  Data-Client-ID: <NORTHBEAM_CLIENT_ID>

Attribution settings (per Leo's spec)
--------------------------------------
  accounting_mode  = accrual
  attribution_window = 1 day (click + view)
  Click vs view split is controlled by the attribution MODEL, not a separate
  window param. Use the account's first available clicks+views model.

Response format
---------------
  The API is async: POST returns a data_export_id. Poll until SUCCESS.
  Result is a presigned GCS URL pointing to a CSV file.
  CSV column names are account-specific; we detect them defensively.
"""

import base64
import csv
import io
import logging
import time
from datetime import date, timedelta
from typing import Any

import requests

import config

log = logging.getLogger(__name__)

_BASE_URL           = "https://api.northbeam.io"
_TIMEOUT            = 30
_POLL_INTERVAL_SECS = 2     # stay well under the 1-req/sec rate limit on the poll endpoint
_POLL_MAX_WAIT_SECS = 300   # 5-minute timeout per job

# Module-level cache for the attribution model ID (fetched once per process)
_cached_model: str | None = None


# ── Auth & base request helpers ───────────────────────────────────────────────

def _headers() -> dict:
    """HTTP Basic auth + required Data-Client-ID header."""
    token = base64.b64encode(f"{config.NORTHBEAM_API_KEY}:".encode()).decode()
    return {
        "Authorization":  f"Basic {token}",
        "Data-Client-ID": config.NORTHBEAM_CLIENT_ID,
        "Content-Type":   "application/json",
        "Accept":         "application/json",
    }


def _get_attribution_model() -> str:
    """
    Discover and cache the account's attribution model ID.
    Prefers a clicks+views model (any model ID containing 'va' or 'view').
    Falls back to the first model returned.
    """
    global _cached_model
    if _cached_model:
        return _cached_model

    resp = requests.get(
        f"{_BASE_URL}/v1/exports/attribution-models",
        headers=_headers(),
        timeout=_TIMEOUT,
    )
    resp.raise_for_status()
    models: list[dict] = resp.json()

    if not models:
        raise RuntimeError("Northbeam returned no attribution models for this account")

    # Prefer clicks+views model
    preferred = next(
        (m["id"] for m in models if "va" in m.get("id", "") or "view" in m.get("id", "").lower()),
        models[0]["id"],
    )
    _cached_model = preferred
    log.info("Northbeam: using attribution model '%s'", _cached_model)
    return _cached_model


# ── Async job lifecycle ───────────────────────────────────────────────────────

def _submit_job(body: dict) -> str:
    """Submit a data export job. Returns data_export_id."""
    resp = requests.post(
        f"{_BASE_URL}/v1/exports/data-export",
        headers=_headers(),
        json=body,
        timeout=_TIMEOUT,
    )
    resp.raise_for_status()
    job_id = resp.json()["data_export_id"]
    log.info("Northbeam: submitted export job %s (level=%s)", job_id, body.get("level"))
    return job_id


def _poll_job(job_id: str) -> str:
    """Poll until the job succeeds. Returns the presigned CSV download URL."""
    deadline = time.monotonic() + _POLL_MAX_WAIT_SECS
    while time.monotonic() < deadline:
        time.sleep(_POLL_INTERVAL_SECS)
        resp = requests.get(
            f"{_BASE_URL}/v1/exports/data-export/result/{job_id}",
            headers=_headers(),
            timeout=_TIMEOUT,
        )
        resp.raise_for_status()
        payload = resp.json()
        status  = payload.get("status")

        if status == "SUCCESS":
            urls = payload.get("result", [])
            if not urls:
                raise RuntimeError(f"Northbeam job {job_id} succeeded but result URL list is empty")
            return urls[0]

        if status in ("FAILED", "ERROR"):
            raise RuntimeError(f"Northbeam export job {job_id} failed: {payload}")

        log.debug("Northbeam: job %s status=%s — waiting %ds", job_id, status, _POLL_INTERVAL_SECS)

    raise TimeoutError(f"Northbeam export job {job_id} did not complete within {_POLL_MAX_WAIT_SECS}s")


def _download_csv(url: str) -> list[dict]:
    """Download presigned CSV and return list of row dicts."""
    resp = requests.get(url, timeout=120)
    resp.raise_for_status()
    rows = list(csv.DictReader(io.StringIO(resp.text)))
    if rows:
        log.debug("Northbeam CSV columns: %s", list(rows[0].keys()))
    return rows


# ── Core export runner ────────────────────────────────────────────────────────

def _run_export(level: str, platform: str, days: int) -> list[dict]:
    """
    Submit a DAILY export job for the given entity level, platform, and date
    range. Returns parsed CSV rows.

    level    — "campaign" | "ad" | "adset"
    platform — "Pinterest" | "Facebook Ads"
    days     — number of days back from yesterday (inclusive)
    """
    end   = date.today() - timedelta(days=1)          # yesterday
    start = end - timedelta(days=days - 1)

    body: dict[str, Any] = {
        "level":            level,
        "time_granularity": "DAILY",
        "start_date":       start.isoformat(),
        "end_date":         end.isoformat(),
        "breakdowns": [
            {"key": "Platform (Northbeam)", "values": [platform]},
        ],
        "options": {
            "export_aggregation":     "BREAKDOWN",
            "remove_zero_spend":      False,
            "aggregate_data":         False,
            "include_ids":            True,   # ensures entity IDs appear in CSV
            "include_kind_and_platform": False,
        },
        "attribution_options": {
            "attribution_models":  [_get_attribution_model()],
            "accounting_modes":    ["accrual"],
            "attribution_windows": ["1"],     # 1-day click + 1-day view
        },
        "metrics": [
            {"id": "spend"},
            {"id": "rev"},
            {"id": "roas"},          # pre-computed by Northbeam (handles spend/rev row merging)
            {"id": "transactions"},
        ],
    }

    job_id  = _submit_job(body)
    csv_url = _poll_job(job_id)
    return _download_csv(csv_url)


# ── CSV parsing helpers ───────────────────────────────────────────────────────

def _find_col(row: dict, candidates: list[str]) -> str:
    """Return the first matching column name from candidates, or ''."""
    for key in candidates:
        if key in row:
            return key
    log.warning("Northbeam CSV: could not find column among %s. Available: %s",
                candidates, list(row.keys()))
    return ""


def _parse_float(val: Any) -> float:
    try:
        return float(val) if val not in (None, "", "null") else 0.0
    except (TypeError, ValueError):
        return 0.0


def _aggregate_by_id(
    rows:       list[dict],
    id_col:     str,
    date_col:   str,
    cutoff:     date,
) -> dict[str, dict]:
    """
    Sum spend/revenue/conversions for rows on or after `cutoff`.
    Returns {entity_id: {spend, revenue, conversions, roas}}.
    """
    totals: dict[str, dict] = {}

    for row in rows:
        entity_id = row.get(id_col, "").strip()
        if not entity_id:
            continue

        row_date_str = row.get(date_col, "")
        try:
            if date.fromisoformat(row_date_str) < cutoff:
                continue
        except ValueError:
            pass  # if we can't parse the date, include the row

        spend       = _parse_float(row.get("spend") or row.get("Spend"))
        revenue     = _parse_float(row.get("rev")   or row.get("Revenue"))
        nb_roas     = _parse_float(row.get("roas")  or row.get("ROAS"))   # Northbeam pre-computed
        conversions = int(_parse_float(row.get("transactions") or row.get("Transactions")))

        if entity_id not in totals:
            totals[entity_id] = {"spend": 0.0, "revenue": 0.0, "nb_roas": 0.0, "conversions": 0, "_days": 0}
        totals[entity_id]["spend"]       += spend
        totals[entity_id]["revenue"]     += revenue
        totals[entity_id]["nb_roas"]     += nb_roas   # accumulated for daily avg
        totals[entity_id]["conversions"] += conversions
        totals[entity_id]["_days"]       += 1

    for v in totals.values():
        days = v.pop("_days") or 1
        # Use Northbeam's pre-computed ROAS (avg across days) when available;
        # fall back to sum(rev)/sum(spend) if the roas column wasn't returned.
        nb_roas_avg = v.pop("nb_roas") / days
        v["roas"] = nb_roas_avg if nb_roas_avg else (v["revenue"] / v["spend"] if v["spend"] else 0.0)

    return totals


def _build_multiwindow(
    rows:    list[dict],
    id_candidates:   list[str],
    date_candidates: list[str],
    windows: tuple[int, ...],
) -> dict[int, dict[str, dict]]:
    """
    From a set of DAILY rows covering max(windows) days, produce a
    multi-window dict by slicing at the appropriate cutoff date per window.
    """
    if not rows:
        return {w: {} for w in windows}

    first    = rows[0]
    id_col   = _find_col(first, id_candidates)
    date_col = _find_col(first, date_candidates)

    if not id_col:
        log.error("Northbeam: cannot build multi-window result — ID column not found")
        return {w: {} for w in windows}

    today  = date.today()
    result = {}
    for w in windows:
        cutoff    = today - timedelta(days=w)   # include last w days (up to yesterday)
        result[w] = _aggregate_by_id(rows, id_col, date_col, cutoff)
    return result


# ── Public API ────────────────────────────────────────────────────────────────

def fetch_multiwindow_campaign_roas(
    windows: tuple[int, ...] = (1, 3, 7),
) -> dict[int, dict[str, dict]]:
    """
    Fetch Pinterest campaign ROAS for 1-, 3-, and 7-day windows in a
    single Northbeam export job (DAILY granularity, last max(windows) days).

    Returns
    -------
    {
        1: {"<pinterest_campaign_id>": {"spend": float, "revenue": float,
                                        "roas": float, "conversions": int}},
        3: {...},
        7: {...},
    }
    """
    try:
        rows = _run_export("campaign", "Pinterest", max(windows))
    except Exception as exc:
        log.error("Northbeam campaign export failed: %s", exc)
        return {w: {} for w in windows}

    return _build_multiwindow(
        rows,
        id_candidates   = ["campaign_id", "Campaign ID", "Campaign", "id", "ID"],
        date_candidates = ["date", "Date", "DATE"],
        windows         = windows,
    )


def fetch_multiwindow_ad_roas(
    windows: tuple[int, ...] = (1, 3, 7),
) -> dict[int, dict[str, dict]]:
    """
    Fetch Pinterest ad-level ROAS for 1-, 3-, and 7-day windows in a
    single Northbeam export job.

    Returns
    -------
    {1: {"<ad_id>": {"spend", "revenue", "roas", "conversions"}}, 3: {...}, 7: {...}}
    """
    try:
        rows = _run_export("ad", "Pinterest", max(windows))
    except Exception as exc:
        log.error("Northbeam ad export failed: %s", exc)
        return {w: {} for w in windows}

    return _build_multiwindow(
        rows,
        id_candidates   = ["ad_id", "Ad ID", "Ad", "id", "ID"],
        date_candidates = ["date", "Date", "DATE"],
        windows         = windows,
    )


def fetch_meta_campaign_roas(
    lookback_days: int = 7,
) -> dict[str, dict]:
    """
    Fetch Meta campaign ROAS from Northbeam for the creative scorer.
    Note: Meta platform string is 'Facebook Ads' (not 'Meta').

    Returns {campaign_id: {spend, revenue, roas, conversions}}
    """
    try:
        rows = _run_export("campaign", "Facebook Ads", lookback_days)
    except Exception as exc:
        log.error("Northbeam Meta campaign export failed: %s", exc)
        return {}

    if not rows:
        return {}

    first  = rows[0]
    id_col = _find_col(first, ["campaign_id", "Campaign ID", "Campaign", "id", "ID"])
    if not id_col:
        log.error("Northbeam Meta: ID column not found")
        return {}

    # No date filtering needed — aggregate all rows
    totals: dict[str, dict] = {}
    for row in rows:
        entity_id   = row.get(id_col, "").strip()
        spend       = _parse_float(row.get("spend")        or row.get("Spend"))
        revenue     = _parse_float(row.get("rev")          or row.get("Revenue"))
        nb_roas     = _parse_float(row.get("roas")         or row.get("ROAS"))
        conversions = int(_parse_float(row.get("transactions") or row.get("Transactions")))
        if entity_id not in totals:
            totals[entity_id] = {"spend": 0.0, "revenue": 0.0, "nb_roas": 0.0, "conversions": 0, "_days": 0}
        totals[entity_id]["spend"]       += spend
        totals[entity_id]["revenue"]     += revenue
        totals[entity_id]["nb_roas"]     += nb_roas
        totals[entity_id]["conversions"] += conversions
        totals[entity_id]["_days"]       += 1

    for v in totals.values():
        days = v.pop("_days") or 1
        nb_roas_avg = v.pop("nb_roas") / days
        v["roas"] = nb_roas_avg if nb_roas_avg else (v["revenue"] / v["spend"] if v["spend"] else 0.0)

    log.info("Northbeam: fetched Meta ROAS for %d campaigns", len(totals))
    return totals


# ── Backwards-compat single-window helpers ────────────────────────────────────

def fetch_campaign_roas(lookback_days: int = 7) -> dict[str, dict]:
    return fetch_multiwindow_campaign_roas(windows=(lookback_days,)).get(lookback_days, {})


def fetch_ad_roas(lookback_days: int = 7) -> dict[str, dict]:
    return fetch_multiwindow_ad_roas(windows=(lookback_days,)).get(lookback_days, {})
