"""
PAMBS — Pinterest Automated Media Buying System
Main orchestrator.

Scheduler
---------
  3:00 AM EST  — Full daily run (all intelligence loops + AI agents)
  7:00 AM EST  — Post morning digest to Slack

Usage
-----
  python -m pinterest_buyer.main          # start scheduler (production)
  python -m pinterest_buyer.main --once   # run immediately once (testing)
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
)
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

    # Step 7: AI agent — anomaly detection
    log.info("Running anomaly detection agent...")
    anomaly_result: dict = {"anomalies": [], "summary": ""}
    try:
        anomaly_result = anomaly_agent.run_anomaly_detection()
    except Exception as exc:
        log.error("Anomaly detection failed: %s", exc)

    # Step 8 (Mondays): Opportunity agent
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
        slack.post_morning_digest(
            f"⚠️ Digest generation failed: {exc}\n\n"
            f"Graduations: {len(_digest_payload['graduation_events'])}, "
            f"Budget changes: {len([d for d in _digest_payload['budget_decisions'] if d.get('action') != 'hold'])}, "
            f"Ads paused: {len(_digest_payload['paused_ads'])}"
        )


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
    args = parser.parse_args()

    log.info("Initialising database...")
    db.init_db()

    if args.once:
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

    log.info(
        "Scheduler started. Daily run at %s EST, digest at %s EST. "
        "Shadow mode: %s.",
        config.SCHEDULE_TIME, config.SLACK_DIGEST_TIME, config.SHADOW_MODE,
    )

    try:
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        log.info("Scheduler stopped.")


if __name__ == "__main__":
    main()
