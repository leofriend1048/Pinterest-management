"""
Northbeam Data Export API connector.

Responsibilities
----------------
- Fetch Pinterest-attributed ROAS, spend, and conversions per campaign and ad
  for the budget engine and ad pause auditor.
- Fetch Meta-attributed creative scoring data for the weekly discovery launcher.

API surface (Northbeam v1 Data Export)
---------------------------------------
  POST /v1/exports/data-export               — submit async export job
  GET  /v1/exports/data-export/result/{id}  — poll for completion
  GET  /v1/exports/attribution-models        — discover model IDs (called once)

Auth
----
  Authorization: Basic <api_key>   (raw key — NOT base64-encoded)
  Data-Client-ID: <NORTHBEAM_CLIENT_ID>

Design
------
  ROAS is a pre-computed metric from Northbeam — it is NOT computed here.
  For multi-window (1d/3d/7d) calls, one TOTAL-granularity job is submitted
  per window with the appropriate date range. Northbeam aggregates the period
  and returns spend, revenue, and ROAS already computed for the window.

Attribution settings (per spec)
--------------------------------
  accounting_mode    = accrual
  attribution_window = ["1"]  (1-day click + 1-day view)
  click vs view split is determined by the attribution MODEL, not a separate
  window param — the account's clicks+views model is auto-discovered on first run.
"""

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
_POLL_INTERVAL_SECS = 2     # stay well under the 1-req/sec poll rate limit
_POLL_MAX_WAIT_SECS = 300   # 5-minute timeout per job

_cached_model: str | None = None


# ── Auth ──────────────────────────────────────────────────────────────────────

def _headers() -> dict:
    # Northbeam uses the raw API key directly after "Basic" — not base64-encoded
    return {
        "Authorization":  f"Basic {config.NORTHBEAM_API_KEY}",
        "Data-Client-ID": config.NORTHBEAM_CLIENT_ID,
        "Content-Type":   "application/json",
        "Accept":         "application/json",
    }


def _get_attribution_model() -> str:
    """
    Discover and cache the account's attribution model ID.
    Prefers any clicks+views model (id containing 'va' or 'view').
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
    raw = resp.json()
    log.debug("Northbeam attribution-models raw response: %s", raw)

    # Normalise to a flat list of model ID strings.
    # API may return: list of strings, list of dicts, or a wrapped dict.
    model_ids = _extract_model_ids(raw)
    if not model_ids:
        raise RuntimeError(
            f"Northbeam returned no attribution models. Raw response: {raw}"
        )

    preferred = next(
        (m for m in model_ids if "va" in m or "view" in m.lower()),
        model_ids[0],
    )
    _cached_model = preferred
    log.info("Northbeam: using attribution model '%s'", _cached_model)
    return _cached_model


def _extract_model_ids(raw: Any) -> list[str]:
    """Normalise the attribution-models API response to a flat list of ID strings."""
    if isinstance(raw, list):
        if raw and isinstance(raw[0], dict):
            return [m.get("id", "") or m.get("model_id", "") for m in raw if m]
        return [str(m) for m in raw if m]
    if isinstance(raw, dict):
        # Try common wrapper keys
        inner = (
            raw.get("data")
            or raw.get("attribution_models")
            or raw.get("results")
            or raw.get("models")
            or []
        )
        return _extract_model_ids(inner)
    return []


# ── Async job lifecycle ───────────────────────────────────────────────────────

def _submit_job(body: dict) -> str:
    resp = requests.post(
        f"{_BASE_URL}/v1/exports/data-export",
        headers=_headers(),
        json=body,
        timeout=_TIMEOUT,
    )
    if not resp.ok:
        log.error("Northbeam submit failed %s: %s", resp.status_code, resp.text[:500])
    resp.raise_for_status()
    job_id = resp.json()["data_export_id"]
    log.info("Northbeam: submitted export job %s (level=%s, days=%s–%s)",
             job_id, body.get("level"), body.get("start_date"), body.get("end_date"))
    return job_id


def _poll_job(job_id: str) -> str:
    """Poll until SUCCESS. Returns the presigned CSV download URL."""
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

        log.debug("Northbeam: job %s status=%s", job_id, status)

    raise TimeoutError(f"Northbeam export job {job_id} did not complete within {_POLL_MAX_WAIT_SECS}s")


def _download_csv(url: str) -> list[dict]:
    resp = requests.get(url, timeout=120)
    resp.raise_for_status()
    rows = list(csv.DictReader(io.StringIO(resp.text)))
    if rows:
        log.debug("Northbeam CSV columns: %s", list(rows[0].keys()))
    return rows


# ── Core export runner ────────────────────────────────────────────────────────

def _run_export(level: str, platform: str, days: int) -> list[dict]:
    """
    Submit a DAILY-granularity export job for the last `days` days, aggregated
    to one row per entity over the period (aggregate_data=True).

    level    — "campaign" | "ad"
    platform — "Pinterest" | "Facebook Ads"  (exact Northbeam breakdown label)
    days     — lookback window (1 = yesterday only, 3 = last 3 days, 7 = last 7 days)

    Metrics requested: spend + rev (confirmed IDs). roas and transactions are
    attempted; if absent in CSV, ROAS is computed from rev/spend and conversions
    default to 0.
    """
    end   = date.today() - timedelta(days=1)      # yesterday
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
            "export_aggregation":        "BREAKDOWN",
            "remove_zero_spend":         False,
            "aggregate_data":            True,   # collapse to one row per entity
            "include_ids":               True,
            "include_kind_and_platform": False,
        },
        "attribution_options": {
            "attribution_models":  [_get_attribution_model()],
            "accounting_modes":    ["accrual"],
            "attribution_windows": [1],
        },
        "metrics": [
            {"id": "spend"},
            {"id": "rev"},
            {"id": "roas"},          # may not be available on all accounts — fallback below
            {"id": "transactions"},  # may not be available on all accounts — fallback to 0
        ],
    }

    job_id  = _submit_job(body)
    csv_url = _poll_job(job_id)
    return _download_csv(csv_url)


# ── CSV parsing ───────────────────────────────────────────────────────────────

def _find_col(row: dict, candidates: list[str]) -> str:
    for key in candidates:
        if key in row:
            return key
    log.warning("Northbeam CSV: column not found among %s. Available: %s",
                candidates, list(row.keys()))
    return ""


def _parse_float(val: Any) -> float:
    try:
        return float(val) if val not in (None, "", "null") else 0.0
    except (TypeError, ValueError):
        return 0.0


def _parse_rows(rows: list[dict], id_candidates: list[str]) -> dict[str, dict]:
    """
    Parse an aggregated Northbeam CSV (one row per entity) into a dict keyed
    by entity ID.

    ROAS: taken from the 'roas'/'ROAS' column when present; otherwise computed
    as rev/spend (mathematically equivalent since 'rev' is Northbeam-attributed
    revenue already weighted by the attribution model).

    Conversions: taken from 'transactions'/'Transactions' when present; defaults
    to 0 if the column is absent (non-critical — only used for informational logging).
    """
    if not rows:
        return {}

    if rows:
        log.debug("Northbeam CSV columns available: %s", list(rows[0].keys()))

    id_col = _find_col(rows[0], id_candidates)
    if not id_col:
        log.error("Northbeam: ID column not found in CSV")
        return {}

    result: dict[str, dict] = {}
    for row in rows:
        entity_id = row.get(id_col, "").strip()
        if not entity_id:
            continue
        spend   = _parse_float(row.get("spend")        or row.get("Spend"))
        revenue = _parse_float(row.get("rev")          or row.get("Revenue")
                               or row.get("attributed_revenue"))
        # ROAS: use API value if present, else compute from rev/spend
        roas = _parse_float(row.get("roas") or row.get("ROAS"))
        if roas == 0.0 and spend > 0 and revenue > 0:
            roas = revenue / spend
        conversions = int(_parse_float(
            row.get("transactions") or row.get("Transactions") or 0
        ))
        result[entity_id] = {
            "spend":       spend,
            "revenue":     revenue,
            "roas":        roas,
            "conversions": conversions,
        }
    return result


# ── Public API ────────────────────────────────────────────────────────────────

def fetch_multiwindow_campaign_roas(
    windows: tuple[int, ...] = (1, 3, 7),
) -> dict[int, dict[str, dict]]:
    """
    Fetch Pinterest campaign ROAS for each window period.
    One Northbeam export job per window — ROAS is returned pre-computed.

    Returns
    -------
    {
        1: {"<campaign_id>": {"spend": float, "revenue": float, "roas": float, "conversions": int}},
        3: {...},
        7: {...},
    }
    """
    result: dict[int, dict[str, dict]] = {}
    for days in windows:
        try:
            rows = _run_export("campaign", "Pinterest", days)
            result[days] = _parse_rows(
                rows,
                id_candidates=["campaign_id", "Campaign ID", "Campaign", "id", "ID"],
            )
            log.info("Northbeam: campaign ROAS fetched for %d-day window (%d campaigns)",
                     days, len(result[days]))
        except Exception as exc:
            log.error("Northbeam campaign export failed (window=%dd): %s", days, exc)
            result[days] = {}
    return result


def fetch_multiwindow_ad_roas(
    windows: tuple[int, ...] = (1, 3, 7),
) -> dict[int, dict[str, dict]]:
    """
    Fetch Pinterest ad-level ROAS for each window period.

    Returns {1: {"<ad_id>": {...}}, 3: {...}, 7: {...}}
    """
    result: dict[int, dict[str, dict]] = {}
    for days in windows:
        try:
            rows = _run_export("ad", "Pinterest", days)
            result[days] = _parse_rows(
                rows,
                id_candidates=["ad_id", "Ad ID", "Ad", "id", "ID"],
            )
            log.info("Northbeam: ad ROAS fetched for %d-day window (%d ads)",
                     days, len(result[days]))
        except Exception as exc:
            log.error("Northbeam ad export failed (window=%dd): %s", days, exc)
            result[days] = {}
    return result


def fetch_meta_campaign_roas(lookback_days: int = 7) -> dict[str, dict]:
    """
    Fetch Meta campaign ROAS from Northbeam for the creative scorer.
    Note: Meta platform string is 'Facebook Ads' (not 'Meta').

    Returns {campaign_id: {spend, revenue, roas, conversions}}
    """
    try:
        rows = _run_export("campaign", "Facebook Ads", lookback_days)
        result = _parse_rows(rows, id_candidates=["campaign_id", "Campaign ID", "Campaign", "id", "ID"])
        log.info("Northbeam: Meta campaign ROAS fetched (%d campaigns)", len(result))
        return result
    except Exception as exc:
        log.error("Northbeam Meta campaign export failed: %s", exc)
        return {}


# ── Connectivity test ─────────────────────────────────────────────────────────

def ping() -> str:
    """Lightweight auth check — discovers and returns the attribution model ID."""
    return _get_attribution_model()


# ── Backwards-compat single-window helpers ────────────────────────────────────

def fetch_campaign_roas(lookback_days: int = 7) -> dict[str, dict]:
    return fetch_multiwindow_campaign_roas(windows=(lookback_days,)).get(lookback_days, {})


def fetch_ad_roas(lookback_days: int = 7) -> dict[str, dict]:
    return fetch_multiwindow_ad_roas(windows=(lookback_days,)).get(lookback_days, {})
