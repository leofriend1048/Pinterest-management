"""
PAMBS — Pinterest Automated Media Buying System
Main orchestrator.

Scheduler
---------
  3:00 AM EST  — Full daily run (all intelligence loops + AI agents)
  7:00 AM EST  — Post morning digest to Slack

Usage
-----
  python -m pinterest_buyer.main               # start scheduler (production / Cloud Run)
  python -m pinterest_buyer.main --once        # run immediately once (testing)
  python -m pinterest_buyer.main --test-apis   # connectivity check for all 4 APIs
  python -m pinterest_buyer.main --clean-db    # wipe + reinitialise state DB
  python -m pinterest_buyer.main --seed        # import existing Pinterest campaigns
  python -m pinterest_buyer.main --digest-only # post morning digest (after --once run)
"""

import argparse
import logging
import sys
from datetime import datetime

import pytz
from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger

import config
from state import db
from intelligence import (
    budget_engine,
    graduation_engine,
    ad_pause_auditor,
    campaign_launcher,
    fatigue_detector,
)
from connectors import pinterest_connector as pinterest
from agents import anomaly_agent, narrative_agent, opportunity_agent
from notifications import slack

logging.basicConfig(
    level=getattr(logging, config.LOG_LEVEL, logging.INFO),
    format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("pinterest_buyer/logs/audit.log"),
    ],
)
log = logging.getLogger("pambs.main")

_TZ = pytz.timezone(config.SCHEDULE_TIMEZONE)

# ── Stored digest state (populated during 3 AM run, posted at 7 AM) ───────────
_digest_payload: dict = {
    "anomaly_result":   {"anomalies": [], "summary": ""},
    "graduation_events": [],
    "budget_decisions": [],
    "paused_ads":       [],
}


def _is_monday() -> bool:
    return datetime.now(_TZ).weekday() == 0


def _is_first_monday() -> bool:
    now = datetime.now(_TZ)
    return now.weekday() == 0 and now.day <= 7


# ── Campaign seeding ──────────────────────────────────────────────────────────

def seed_existing_campaigns() -> None:
    """
    Discover existing Pinterest campaigns and mirror them into campaign_log.

    Safe to call repeatedly — uses upsert so existing records are not overwritten.

    Brand detection uses BRAND_CAMPAIGN_KEYWORDS (keyword match on campaign name).
    Campaign-type detection maps name fragments to internal type keys.
    ACTIVE campaigns are seeded as 'active'; all others as 'paused'.
    Only active campaigns are managed by PAMBS; paused ones are recorded for
    historical context but excluded from budget optimisation.
    """
    from datetime import date as _date
    log.info("Seeding existing Pinterest campaigns from API...")

    # Map name fragments → internal campaign_type keys (checked in order)
    _type_hints: list[tuple[str, str]] = [
        ("actalike",         "scale_actalike"),
        ("purchaseractalike","scale_actalike"),
        ("pplus",            "scale_pplus"),
        ("p+",               "scale_pplus"),
        ("performance_plus", "scale_pplus"),
        ("performance plus", "scale_pplus"),
        ("perf+",            "scale_pplus"),
        ("kw only",          "scale_keyword"),
        ("kw_",              "scale_keyword"),
        ("keyword",          "scale_keyword"),
        ("broad",            "scale_broad"),
        ("interest",         "scale_broad"),
        ("beauty interest",  "scale_broad"),
        ("discovery",        "discovery"),
        ("weekly",           "discovery"),
        ("test",             "discovery"),
        ("challenger",       "scale_pplus"),
        ("retargeting",      "discovery"),   # closest PAMBS type for existing retarg
        ("--rem--",          "discovery"),
        ("--pro--",          "discovery"),
    ]

    try:
        campaigns = pinterest.list_campaigns()
    except Exception as exc:
        log.error("Failed to fetch campaigns from Pinterest: %s", exc)
        return

    seeded = 0
    skipped = 0
    for c in campaigns:
        name_lower  = c.get("name", "").lower()
        campaign_id = c["id"]

        # ── Brand detection: keyword match (BRAND_CAMPAIGN_KEYWORDS order matters) ──
        brand = None
        for b, keywords in config.BRAND_CAMPAIGN_KEYWORDS.items():
            if any(kw in name_lower for kw in keywords):
                brand = b
                break

        # Fallback: if account has only one brand, assign unmatched campaigns to it
        if not brand and len(config.BRANDS) == 1:
            brand = config.BRANDS[0]

        if not brand:
            log.debug("Skipping unrecognised brand in campaign: %s", c.get("name"))
            skipped += 1
            continue

        # ── Campaign-type detection ───────────────────────────────────────────
        ctype = next(
            (v for k, v in _type_hints if k in name_lower),
            "discovery",   # safe default
        )

        status       = "active" if c.get("status") == "ACTIVE" else "paused"
        budget_micro = c.get("daily_spend_cap") or 0
        budget_usd   = budget_micro / 1_000_000

        db.upsert_campaign(
            {
                "campaign_id":           campaign_id,
                "brand":                 brand,
                "campaign_type":         ctype,
                "pinterest_campaign_id": campaign_id,
                "status":                status,
                "budget_usd":            budget_usd or config.CAMPAIGN_BUDGETS.get(ctype, 300),
                "launch_date":           _date.today().isoformat(),
                "is_challenger":         1 if "challenger" in name_lower else 0,
            }
        )
        log.info("Seeded: %-70s → brand=%-20s type=%-15s status=%s $%.0f/day",
                 c.get("name"), brand, ctype, status, budget_usd)
        seeded += 1

    log.info("Seeding complete: %d campaigns mirrored, %d skipped (unrecognised brand).",
             seeded, skipped)


# ── Daypart + keyword ingestion ───────────────────────────────────────────────

def _ingest_daypart_and_keywords() -> None:
    """
    Fetch yesterday's HOUR-granularity and keyword analytics from Pinterest
    and persist them for the fatigue detector and opportunity agent.
    Runs as part of the daily 3 AM job after budget optimization.
    """
    from datetime import date as _date, timedelta as _td
    yesterday = (_date.today() - _td(days=1)).isoformat()

    all_campaigns = db.get_active_campaigns()
    if not all_campaigns:
        return

    # ── Daypart ingestion ─────────────────────────────────────────────────────
    all_pin_ids = [
        c.get("pinterest_campaign_id") or c["campaign_id"]
        for c in all_campaigns
    ]

    try:
        hourly_rows = pinterest.get_hourly_campaign_analytics(all_pin_ids, yesterday)
    except Exception as exc:
        log.warning("Hourly analytics fetch failed: %s", exc)
        hourly_rows = []

    # Aggregate by (brand, hour)
    brand_hourly: dict[str, dict[int, dict]] = {}
    pin_to_brand  = {
        (c.get("pinterest_campaign_id") or c["campaign_id"]): c["brand"]
        for c in all_campaigns
    }
    for row in hourly_rows:
        brand = pin_to_brand.get(row["campaign_id"])
        if not brand:
            continue
        h = row["hour_utc"]
        brand_hourly.setdefault(brand, {}).setdefault(h, {"spend_usd": 0.0, "revenue_usd": 0.0})
        brand_hourly[brand][h]["spend_usd"]   += row["spend"]
        brand_hourly[brand][h]["revenue_usd"] += row["revenue"]

    for brand, hours in brand_hourly.items():
        hourly_list = [{"hour_utc": h, **v} for h, v in hours.items()]
        fatigue_detector.ingest_daypart_data(brand, hourly_list)
        log.info("[%s] Ingested daypart data for %d hours.", brand, len(hourly_list))

    # ── Keyword ingestion ─────────────────────────────────────────────────────
    # Only pull from keyword-type campaigns (they have actual keyword targeting)
    kw_campaigns = [c for c in all_campaigns if c["campaign_type"] in ("scale_keyword", "scale_broad")]
    if not kw_campaigns:
        return

    from datetime import date as _date2, timedelta as _td2
    start = (_date2.today() - _td2(days=1)).isoformat()

    for camp in kw_campaigns:
        brand       = camp["brand"]
        pin_camp_id = camp.get("pinterest_campaign_id") or camp["campaign_id"]
        try:
            ads = pinterest.list_ads_in_campaign(pin_camp_id)
            ad_ids = [a["id"] for a in ads if a.get("status") == "ACTIVE"]
            if not ad_ids:
                continue
            kw_rows = pinterest.get_keyword_analytics(ad_ids, start, yesterday)
            for kw in kw_rows:
                db.upsert_keyword_performance(
                    {
                        "brand":       brand,
                        "keyword":     kw["keyword"],
                        "impressions": kw["impressions"],
                        "clicks":      kw["clicks"],
                        "conversions": kw["conversions"],
                        "spend_usd":   kw["spend"],
                        "revenue_usd": kw["revenue"],
                        "roas":        kw["roas"],
                    }
                )
            log.info("[%s] Ingested %d keyword rows.", brand, len(kw_rows))
        except Exception as exc:
            log.warning("[%s] Keyword ingestion failed for campaign %s: %s",
                        brand, pin_camp_id, exc)


# ── Core daily run ────────────────────────────────────────────────────────────

def daily_run() -> None:
    """
    Full 3 AM daily run.

    Order of operations:
    1. Ensure scale campaigns exist for all brands
    2. Weekly discovery launch (Mondays only)
    3. Monthly Performance+ challenger (first Monday only)
    4. Scale graduation check
    5. Budget optimization
    6. Ad-level pause audit
    7. AI agent run (anomaly → narrative prep)
    """
    global _digest_payload

    start_ts = datetime.now(_TZ)
    log.info("═══ PAMBS daily run starting at %s (%s mode) ═══",
             start_ts.strftime("%Y-%m-%d %H:%M %Z"),
             "SHADOW" if config.SHADOW_MODE else "LIVE")

    all_graduation_events: list[dict] = []

    # Step 1: Ensure scale campaigns exist (idempotent bootstrap)
    for brand in config.BRANDS:
        try:
            campaign_launcher.ensure_scale_campaigns_exist(brand)
        except Exception as exc:
            log.error("[%s] Scale campaign bootstrap failed: %s", brand, exc)

    # Step 2: Weekly discovery launch (Mondays only)
    if _is_monday():
        log.info("Monday — launching weekly discovery campaigns.")
        for brand in config.BRANDS:
            try:
                campaign_launcher.launch_weekly_discovery(brand)
            except Exception as exc:
                log.error("[%s] Discovery launch failed: %s", brand, exc)

    # Step 3: Monthly Performance+ challenger (first Monday only)
    if _is_first_monday():
        log.info("First Monday — launching monthly Performance+ challengers.")
        for brand in config.BRANDS:
            try:
                campaign_launcher.launch_monthly_challenger(brand)
            except Exception as exc:
                log.error("[%s] Challenger launch failed: %s", brand, exc)

    # Step 4: Scale graduation check
    log.info("Running graduation check...")
    for brand in config.BRANDS:
        try:
            events = graduation_engine.run_graduation_check(brand)
            all_graduation_events.extend(events)
        except Exception as exc:
            log.error("[%s] Graduation check failed: %s", brand, exc)

    # Step 5: Budget optimization
    log.info("Running budget optimization...")
    budget_decisions: list[dict] = []
    try:
        budget_decisions = budget_engine.run_budget_optimization()
    except Exception as exc:
        log.error("Budget optimization failed: %s", exc)

    # Step 6: Ad-level pause audit
    log.info("Running ad-level pause audit...")
    paused_ads: list[dict] = []
    try:
        paused_ads = ad_pause_auditor.run_ad_pause_audit()
    except Exception as exc:
        log.error("Ad pause audit failed: %s", exc)

    # Step 7: Daypart + keyword ingestion (feeds fatigue detector & opportunity agent)
    log.info("Ingesting daypart and keyword data...")
    try:
        _ingest_daypart_and_keywords()
    except Exception as exc:
        log.error("Daypart/keyword ingestion failed: %s", exc)

    # Step 8: AI agent — anomaly detection
    log.info("Running anomaly detection agent...")
    anomaly_result: dict = {"anomalies": [], "summary": ""}
    try:
        anomaly_result = anomaly_agent.run_anomaly_detection()
    except Exception as exc:
        log.error("Anomaly detection failed: %s", exc)

    # Step 9 (Mondays): Opportunity agent
    if _is_monday():
        log.info("Monday — running opportunity agent...")
        try:
            opportunity_agent.run_opportunity_analysis()
        except Exception as exc:
            log.error("Opportunity agent failed: %s", exc)

    # Store for the 7 AM digest post
    _digest_payload = {
        "anomaly_result":    anomaly_result,
        "graduation_events": all_graduation_events,
        "budget_decisions":  budget_decisions,
        "paused_ads":        paused_ads,
    }

    # If shadow mode, post a Slack summary of what would have happened
    if config.SHADOW_MODE:
        simulated = [d for d in budget_decisions if d.get("action") != "hold"]
        simulated += [{"action": "pause_ad", **a} for a in paused_ads]
        simulated += [{"action": "graduation", **g} for g in all_graduation_events]
        if simulated:
            slack.post_shadow_summary(simulated)

    elapsed = (datetime.now(_TZ) - start_ts).total_seconds()
    log.info("═══ PAMBS daily run complete in %.0fs ═══", elapsed)


def post_morning_digest() -> None:
    """7 AM job — write and post the morning digest."""
    log.info("Generating morning digest...")
    try:
        digest_text = narrative_agent.write_morning_digest(
            anomaly_result=_digest_payload["anomaly_result"],
            graduation_events=_digest_payload["graduation_events"],
            budget_decisions=_digest_payload["budget_decisions"],
            paused_ads=_digest_payload["paused_ads"],
        )
        slack.post_morning_digest(digest_text)
    except Exception as exc:
        log.error("Morning digest failed: %s", exc)
        try:
            slack.post_morning_digest(
                f"⚠️ Digest generation failed: {exc}\n\n"
                f"Graduations: {len(_digest_payload['graduation_events'])}, "
                f"Budget changes: {len([d for d in _digest_payload['budget_decisions'] if d.get('action') != 'hold'])}, "
                f"Ads paused: {len(_digest_payload['paused_ads'])}"
            )
        except Exception:
            log.error("Slack fallback post also failed — digest not delivered.")


# ── API connectivity check ────────────────────────────────────────────────────

def test_apis() -> None:
    """
    Quick connectivity check for all 4 external APIs.
    Exits with code 1 if any check fails — safe to call before deploying.
    """
    import sys
    from connectors import northbeam_connector as northbeam
    from connectors import meta_connector as meta

    W = 60
    results: list[tuple[str, bool, str]] = []

    def _check(label: str, fn):
        print(f"  Checking {label}...", flush=True)
        try:
            detail = fn()
            results.append((label, True, str(detail)))
        except Exception as exc:
            results.append((label, False, str(exc)[:120]))

    _check("Pinterest API",
           lambda: f"{len(pinterest.list_campaigns())} campaigns found")
    _check("Northbeam API",
           lambda: f"attribution model = {northbeam.ping()}")
    _check("Meta API",
           lambda: f"ad account accessible ({meta.ping()} ad returned)")
    _check("Anthropic API",
           lambda: _anthropic_ping())

    print("\n" + "═" * W)
    print("  PAMBS API Connectivity Report")
    print("═" * W)
    all_pass = True
    for name, ok, detail in results:
        icon   = "✅" if ok else "❌"
        status = "PASS" if ok else "FAIL"
        print(f"  {icon}  {name:<16} {status}   {detail}")
        if not ok:
            all_pass = False
    print("═" * W)
    if all_pass:
        print("  All APIs connected — safe to flip SHADOW_MODE=false and go live.\n")
    else:
        print("  Fix the failures above before deploying.\n")
        sys.exit(1)


def _anthropic_ping() -> str:
    import anthropic
    client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)
    msg = client.messages.create(
        model=config.CLAUDE_MODEL,
        max_tokens=5,
        messages=[{"role": "user", "content": "ping"}],
    )
    return f"model={config.CLAUDE_MODEL} ok (stop_reason={msg.stop_reason})"


def debug_northbeam() -> None:
    """
    Print the live Northbeam API catalogue.
    - Attribution models (with IDs)
    - All available metric IDs + required-metric check
    - Platform breakdown values
    """
    import requests as _req
    from connectors import northbeam_connector as nb

    headers = nb._headers()
    base    = nb._BASE_URL
    W = 72

    # Metric IDs the connector actually requests — verify they exist on this account
    REQUIRED = {"spend", "rev", "roas", "transactions"}

    print("\n" + "═" * W)
    print("  Northbeam API Catalogue")
    print("═" * W)

    # ── Attribution models ────────────────────────────────────────────────────
    print("\n  Attribution Models:")
    try:
        r = _req.get(f"{base}/v1/exports/attribution-models", headers=headers, timeout=15)
        r.raise_for_status()
        raw = r.json()
        items = raw.get("attribution_models") or raw if isinstance(raw, list) else []
        for m in items:
            mid  = m.get("id", m) if isinstance(m, dict) else str(m)
            name = m.get("name", "") if isinstance(m, dict) else ""
            star = " ← selected" if "va" in mid else ""
            print(f"    {mid:<44s}  {name}{star}")
    except Exception as exc:
        print(f"    ❌ {exc}")

    # ── Metrics ───────────────────────────────────────────────────────────────
    print("\n  Available Metrics (all):")
    try:
        r = _req.get(f"{base}/v1/exports/metrics", headers=headers, timeout=15)
        r.raise_for_status()
        raw = r.json()
        items = raw.get("metrics") or raw if isinstance(raw, list) else []
        all_ids: set[str] = set()
        for m in items:
            mid   = m.get("id", "") if isinstance(m, dict) else str(m)
            label = m.get("label", "") if isinstance(m, dict) else ""
            all_ids.add(mid)
            req_marker = " ◄ REQUIRED" if mid in REQUIRED else ""
            print(f"    {mid:<44s}  {label}{req_marker}")
        print(f"\n  Required metric check ({len(REQUIRED)} metrics):")
        for mid in sorted(REQUIRED):
            status = "✅ found" if mid in all_ids else "❌ NOT FOUND — connector will fall back"
            print(f"    {mid:<20s}  {status}")
    except Exception as exc:
        print(f"    ❌ {exc}")

    # ── Breakdowns ────────────────────────────────────────────────────────────
    print("\n  Platform (Northbeam) breakdown values:")
    try:
        r = _req.get(f"{base}/v1/exports/breakdowns", headers=headers, timeout=15)
        r.raise_for_status()
        raw = r.json()
        items = raw.get("breakdowns") or raw if isinstance(raw, list) else []
        for bd in items:
            if not isinstance(bd, dict):
                continue
            key = bd.get("key", "")
            if "platform" in key.lower():
                for v in bd.get("values", []):
                    marker = " ← used" if v in ("Pinterest", "Facebook Ads") else ""
                    print(f"    {v!r}{marker}")
    except Exception as exc:
        print(f"    ❌ {exc}")

    print("\n" + "═" * W + "\n")


# ── DB reset ──────────────────────────────────────────────────────────────────

def clean_db() -> None:
    """Wipe the state DB and initialise a fresh schema. Prompts for confirmation."""
    import os
    db_path = config.DB_PATH
    if os.path.exists(db_path):
        confirm = input(
            f"\n  ⚠️  This will DELETE {db_path} and ALL historical state.\n"
            "  Type 'yes' to confirm: "
        ).strip().lower()
        if confirm != "yes":
            print("  Aborted.")
            return
        os.remove(db_path)
        log.info("Deleted %s", db_path)
    db.init_db()
    print(f"  ✅ Fresh database initialised at {db_path}")


# ── Cloud Run health server ───────────────────────────────────────────────────

def _start_health_server() -> None:
    """
    Minimal HTTP server on $PORT (default 8080) for Cloud Run health checks.
    Responds 200 OK to any GET. Runs in a background daemon thread.
    Harmless when running locally (simply listens and waits).
    """
    import os
    import threading
    from http.server import BaseHTTPRequestHandler, HTTPServer

    class _Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"OK")

        def log_message(self, *args):
            pass  # suppress per-request noise

    port = int(os.environ.get("PORT", 8080))
    server = HTTPServer(("0.0.0.0", port), _Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    log.info("Health server listening on :%d  (Cloud Run ready)", port)


# ── Entry point ───────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="PAMBS orchestrator")
    parser.add_argument(
        "--once",
        action="store_true",
        help="Run daily_run() immediately once, then exit (useful for testing).",
    )
    parser.add_argument(
        "--digest-only",
        action="store_true",
        help="Only post the morning digest (requires a prior --once run).",
    )
    parser.add_argument(
        "--seed",
        action="store_true",
        help="Discover existing Pinterest campaigns and seed them into campaign_log, then exit.",
    )
    parser.add_argument(
        "--test-apis",
        action="store_true",
        help="Connectivity check for all 4 external APIs (Pinterest, Northbeam, Meta, Anthropic). Exits 1 on failure.",
    )
    parser.add_argument(
        "--clean-db",
        action="store_true",
        help="Wipe the state database and reinitialise a fresh schema, then exit.",
    )
    parser.add_argument(
        "--debug-northbeam",
        action="store_true",
        help="Print live Northbeam API catalogue (attribution models, metric IDs, breakdown labels), then exit.",
    )
    args = parser.parse_args()

    # --test-apis / --debug-northbeam: no DB needed, run immediately
    if args.test_apis:
        test_apis()
        return

    if args.debug_northbeam:
        debug_northbeam()
        return

    log.info("Initialising database...")
    db.init_db()

    if args.clean_db:
        clean_db()
        return

    if args.seed:
        seed_existing_campaigns()
        return

    if args.once:
        seed_existing_campaigns()   # safe to call every time — upserts only
        daily_run()
        post_morning_digest()
        return

    if args.digest_only:
        post_morning_digest()
        return

    # Production scheduler
    scheduler = BlockingScheduler(timezone=_TZ)

    # 3:00 AM EST — full daily run
    h3, m3 = config.SCHEDULE_TIME.split(":")
    scheduler.add_job(
        daily_run,
        trigger=CronTrigger(hour=int(h3), minute=int(m3), timezone=_TZ),
        id="daily_run",
        name="PAMBS daily run",
        misfire_grace_time=600,
        coalesce=True,
    )

    # 7:00 AM EST — post morning digest
    h7, m7 = config.SLACK_DIGEST_TIME.split(":")
    scheduler.add_job(
        post_morning_digest,
        trigger=CronTrigger(hour=int(h7), minute=int(m7), timezone=_TZ),
        id="morning_digest",
        name="PAMBS morning digest",
        misfire_grace_time=600,
        coalesce=True,
    )

    _start_health_server()   # Cloud Run health check endpoint

    log.info(
        "Scheduler started. Daily run at %s EST, digest at %s EST. "
        "Shadow mode: %s. Slack: %s.",
        config.SCHEDULE_TIME, config.SLACK_DIGEST_TIME,
        config.SHADOW_MODE, "enabled" if config.SLACK_ENABLED else "disabled",
    )

    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        log.info("Scheduler stopped.")


if __name__ == "__main__":
    main()
