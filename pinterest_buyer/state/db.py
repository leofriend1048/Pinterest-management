"""
SQLite state layer for PAMBS.

Tables
------
creative_mirror        – Meta creative ID → Pinterest pin IDs, scores, graduation status
campaign_log           – All active campaigns, current budget, brand, type
decision_audit         – Every budget decision with rationale
performance_snapshots  – Daily metrics per campaign and ad
graduation_log         – Creative graduation events
keyword_performance    – Keyword-level stats per brand
daypart_log            – Hourly spend/revenue for dayparting analysis
agent_action_log       – Every tool call: rationale, predicted outcome, actual outcome
calibration_findings   – Systematic prediction errors surfaced by calibration agent
experiments            – Hypothesis experiments proposed and tracked
learnings              – Experiment results and persistent learnings
hypothesis_backlog     – Proposed experiments awaiting human approval
"""

import sqlite3
import json
import logging
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path

import config

log = logging.getLogger(__name__)


def _connect() -> sqlite3.Connection:
    Path(config.DB_PATH).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(config.DB_PATH, detect_types=sqlite3.PARSE_DECLTYPES)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


@contextmanager
def get_db():
    conn = _connect()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db() -> None:
    with get_db() as conn:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS creative_mirror (
            meta_creative_id    TEXT PRIMARY KEY,
            brand               TEXT NOT NULL,
            pinterest_pin_ids   TEXT DEFAULT '{}',   -- JSON: {campaign_type: pin_id}
            composite_score     REAL,
            graduation_status   TEXT DEFAULT 'pending',  -- pending | graduated | paused
            first_seen_date     TEXT,
            last_scored_date    TEXT,
            meta_spend_usd      REAL DEFAULT 0,
            meta_roas           REAL DEFAULT 0,
            meta_ctr            REAL DEFAULT 0,
            meta_hook_rate      REAL DEFAULT 0,
            meta_ncpa           REAL DEFAULT 0,
            asset_local_path    TEXT,
            creative_format     TEXT,
            created_at          TEXT DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS campaign_log (
            campaign_id         TEXT PRIMARY KEY,
            brand               TEXT NOT NULL,
            campaign_type       TEXT NOT NULL,
            pinterest_campaign_id TEXT,
            status              TEXT DEFAULT 'active',  -- active | paused | ended
            budget_usd          REAL NOT NULL,
            launch_date         TEXT,
            last_budget_change  TEXT,
            is_challenger       INTEGER DEFAULT 0,
            created_at          TEXT DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS decision_audit (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp           TEXT NOT NULL,
            campaign_id         TEXT NOT NULL,
            brand               TEXT NOT NULL,
            campaign_type       TEXT NOT NULL,
            action              TEXT NOT NULL,   -- scale_up | nudge_up | hold | nudge_down | pause
            prior_budget_usd    REAL,
            new_budget_usd      REAL,
            roas_basis          REAL,
            target_roas         REAL,
            roas_ratio          REAL,
            rationale           TEXT,
            shadow_mode         INTEGER DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS performance_snapshots (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            snapshot_date       TEXT NOT NULL,
            campaign_id         TEXT,
            ad_id               TEXT,
            brand               TEXT NOT NULL,
            campaign_type       TEXT,
            spend_usd           REAL DEFAULT 0,
            revenue_usd         REAL DEFAULT 0,
            roas                REAL DEFAULT 0,
            ncpa                REAL DEFAULT 0,
            impressions         INTEGER DEFAULT 0,
            clicks              INTEGER DEFAULT 0,
            ctr                 REAL DEFAULT 0,
            frequency           REAL DEFAULT 0,
            conversions         INTEGER DEFAULT 0,
            created_at          TEXT DEFAULT (datetime('now'))
        );

        CREATE INDEX IF NOT EXISTS idx_perf_date_brand
            ON performance_snapshots(snapshot_date, brand);
        CREATE INDEX IF NOT EXISTS idx_perf_campaign
            ON performance_snapshots(campaign_id, snapshot_date);

        CREATE TABLE IF NOT EXISTS graduation_log (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            meta_creative_id    TEXT NOT NULL,
            brand               TEXT NOT NULL,
            graduation_date     TEXT NOT NULL,
            spend_at_graduation REAL NOT NULL,
            roas_at_graduation  REAL NOT NULL,
            scale_campaigns     TEXT,  -- JSON list of campaign IDs
            created_at          TEXT DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS keyword_performance (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            snapshot_date       TEXT NOT NULL,
            brand               TEXT NOT NULL,
            keyword             TEXT NOT NULL,
            impressions         INTEGER DEFAULT 0,
            clicks              INTEGER DEFAULT 0,
            conversions         INTEGER DEFAULT 0,
            spend_usd           REAL DEFAULT 0,
            revenue_usd         REAL DEFAULT 0,
            roas                REAL DEFAULT 0,
            created_at          TEXT DEFAULT (datetime('now'))
        );

        CREATE UNIQUE INDEX IF NOT EXISTS idx_kw_date_brand_kw
            ON keyword_performance(snapshot_date, brand, keyword);

        CREATE TABLE IF NOT EXISTS daypart_log (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            log_date            TEXT NOT NULL,
            hour_utc            INTEGER NOT NULL,
            brand               TEXT NOT NULL,
            spend_usd           REAL DEFAULT 0,
            revenue_usd         REAL DEFAULT 0,
            created_at          TEXT DEFAULT (datetime('now'))
        );

        CREATE UNIQUE INDEX IF NOT EXISTS idx_daypart_unique
            ON daypart_log(log_date, hour_utc, brand);

        CREATE TABLE IF NOT EXISTS agent_action_log (
            id                INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp         TEXT DEFAULT (datetime('now')),
            tool_name         TEXT NOT NULL,
            brand             TEXT,
            params_json       TEXT,
            rationale         TEXT,
            predicted_outcome TEXT,
            actual_outcome    TEXT,
            executed          INTEGER DEFAULT 0,
            measured          INTEGER DEFAULT 0,
            source            TEXT DEFAULT 'orchestrator'
        );

        CREATE INDEX IF NOT EXISTS idx_action_log_brand
            ON agent_action_log(brand, timestamp);

        CREATE TABLE IF NOT EXISTS calibration_findings (
            id                    INTEGER PRIMARY KEY AUTOINCREMENT,
            finding_type          TEXT NOT NULL,
            pattern               TEXT,
            evidence              TEXT,
            recommended_correction TEXT,
            action_ids            TEXT,
            created_at            TEXT DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS experiments (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            brand           TEXT,
            campaign_type   TEXT,
            experiment_type TEXT,
            hypothesis      TEXT,
            setup_json      TEXT,
            status          TEXT DEFAULT 'proposed',
            created_at      TEXT DEFAULT (datetime('now')),
            updated_at      TEXT DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS learnings (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            experiment_id INTEGER REFERENCES experiments(id),
            brand         TEXT,
            campaign_type TEXT,
            learning      TEXT NOT NULL,
            evidence      TEXT,
            created_at    TEXT DEFAULT (datetime('now'))
        );

        CREATE INDEX IF NOT EXISTS idx_learnings_brand
            ON learnings(brand, campaign_type);

        CREATE TABLE IF NOT EXISTS hypothesis_backlog (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            hypothesis      TEXT NOT NULL,
            rationale       TEXT,
            experiment_type TEXT,
            brand           TEXT,
            campaign_type   TEXT,
            setup_json      TEXT,
            status          TEXT DEFAULT 'proposed',
            proposed_by     TEXT DEFAULT 'agent',
            created_at      TEXT DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS northbeam_daily_stats (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            stat_date    TEXT NOT NULL,
            level        TEXT NOT NULL,   -- 'campaign' | 'ad'
            entity_id    TEXT NOT NULL,
            entity_name  TEXT,
            spend        REAL DEFAULT 0,
            revenue      REAL DEFAULT 0,
            roas         REAL DEFAULT 0,
            conversions  INTEGER DEFAULT 0,
            new_visitors REAL DEFAULT 0,
            created_at   TEXT DEFAULT (datetime('now'))
        );

        CREATE UNIQUE INDEX IF NOT EXISTS idx_nb_daily_unique
            ON northbeam_daily_stats(stat_date, level, entity_id);

        CREATE TABLE IF NOT EXISTS pinterest_ad_group_stats (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            stat_date     TEXT NOT NULL,
            ad_group_id   TEXT NOT NULL,
            ad_group_name TEXT,
            campaign_id   TEXT NOT NULL,
            brand         TEXT,
            campaign_type TEXT,
            spend_usd     REAL DEFAULT 0,
            revenue_usd   REAL DEFAULT 0,
            roas          REAL DEFAULT 0,
            impressions   INTEGER DEFAULT 0,
            clicks        INTEGER DEFAULT 0,
            ctr           REAL DEFAULT 0,
            conversions   INTEGER DEFAULT 0,
            ncpa          REAL DEFAULT 0,
            created_at    TEXT DEFAULT (datetime('now'))
        );

        CREATE UNIQUE INDEX IF NOT EXISTS idx_pag_daily_unique
            ON pinterest_ad_group_stats(stat_date, ad_group_id);
        """)
    log.info("Database initialised at %s", config.DB_PATH)


# ── creative_mirror helpers ───────────────────────────────────────────────────

def upsert_creative(creative: dict) -> None:
    with get_db() as conn:
        conn.execute("""
            INSERT INTO creative_mirror (
                meta_creative_id, brand, composite_score,
                first_seen_date, last_scored_date,
                meta_spend_usd, meta_roas, meta_ctr,
                meta_hook_rate, meta_ncpa, asset_local_path, creative_format
            ) VALUES (
                :meta_creative_id, :brand, :composite_score,
                :first_seen_date, :last_scored_date,
                :meta_spend_usd, :meta_roas, :meta_ctr,
                :meta_hook_rate, :meta_ncpa, :asset_local_path, :creative_format
            )
            ON CONFLICT(meta_creative_id) DO UPDATE SET
                composite_score   = excluded.composite_score,
                last_scored_date  = excluded.last_scored_date,
                meta_spend_usd    = excluded.meta_spend_usd,
                meta_roas         = excluded.meta_roas,
                meta_ctr          = excluded.meta_ctr,
                meta_hook_rate    = excluded.meta_hook_rate,
                meta_ncpa         = excluded.meta_ncpa,
                asset_local_path  = COALESCE(excluded.asset_local_path, creative_mirror.asset_local_path),
                creative_format   = COALESCE(excluded.creative_format, creative_mirror.creative_format)
        """, creative)


def get_unmirrored_creative_ids(brand: str) -> set:
    """Return Meta creative IDs not yet mirrored to Pinterest for this brand."""
    with get_db() as conn:
        rows = conn.execute(
            "SELECT meta_creative_id FROM creative_mirror WHERE brand=? AND graduation_status='pending'",
            (brand,)
        ).fetchall()
    return {r["meta_creative_id"] for r in rows}


def mark_creative_graduated(meta_creative_id: str) -> None:
    with get_db() as conn:
        conn.execute(
            "UPDATE creative_mirror SET graduation_status='graduated' WHERE meta_creative_id=?",
            (meta_creative_id,)
        )


def set_creative_pin_id(meta_creative_id: str, campaign_type: str, pin_id: str) -> None:
    with get_db() as conn:
        row = conn.execute(
            "SELECT pinterest_pin_ids FROM creative_mirror WHERE meta_creative_id=?",
            (meta_creative_id,)
        ).fetchone()
        pins = json.loads(row["pinterest_pin_ids"]) if row else {}
        pins[campaign_type] = pin_id
        conn.execute(
            "UPDATE creative_mirror SET pinterest_pin_ids=? WHERE meta_creative_id=?",
            (json.dumps(pins), meta_creative_id)
        )


# ── campaign_log helpers ──────────────────────────────────────────────────────

def upsert_campaign(campaign: dict) -> None:
    with get_db() as conn:
        conn.execute("""
            INSERT INTO campaign_log (
                campaign_id, brand, campaign_type, pinterest_campaign_id,
                status, budget_usd, launch_date, is_challenger
            ) VALUES (
                :campaign_id, :brand, :campaign_type, :pinterest_campaign_id,
                :status, :budget_usd, :launch_date, :is_challenger
            )
            ON CONFLICT(campaign_id) DO UPDATE SET
                status              = excluded.status,
                budget_usd          = excluded.budget_usd,
                pinterest_campaign_id = COALESCE(excluded.pinterest_campaign_id,
                                                  campaign_log.pinterest_campaign_id),
                last_budget_change  = datetime('now')
        """, campaign)


def get_active_campaigns(brand: str | None = None) -> list[dict]:
    with get_db() as conn:
        if brand:
            rows = conn.execute(
                "SELECT * FROM campaign_log WHERE status='active' AND brand=?", (brand,)
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM campaign_log WHERE status='active'"
            ).fetchall()
    return [dict(r) for r in rows]


def update_campaign_budget(campaign_id: str, new_budget: float) -> None:
    with get_db() as conn:
        conn.execute(
            "UPDATE campaign_log SET budget_usd=?, last_budget_change=datetime('now') WHERE campaign_id=?",
            (new_budget, campaign_id)
        )


def pause_campaign(campaign_id: str) -> None:
    with get_db() as conn:
        conn.execute(
            "UPDATE campaign_log SET status='paused' WHERE campaign_id=?",
            (campaign_id,)
        )


# ── decision_audit helpers ────────────────────────────────────────────────────

def log_decision(record: dict) -> None:
    record.setdefault("timestamp", datetime.now(timezone.utc).isoformat())
    record.setdefault("shadow_mode", int(config.SHADOW_MODE))
    with get_db() as conn:
        conn.execute("""
            INSERT INTO decision_audit (
                timestamp, campaign_id, brand, campaign_type, action,
                prior_budget_usd, new_budget_usd, roas_basis,
                target_roas, roas_ratio, rationale, shadow_mode
            ) VALUES (
                :timestamp, :campaign_id, :brand, :campaign_type, :action,
                :prior_budget_usd, :new_budget_usd, :roas_basis,
                :target_roas, :roas_ratio, :rationale, :shadow_mode
            )
        """, record)


# ── performance_snapshots helpers ─────────────────────────────────────────────

def insert_performance_snapshot(snapshot: dict) -> None:
    snapshot.setdefault("snapshot_date", datetime.now(timezone.utc).strftime("%Y-%m-%d"))
    with get_db() as conn:
        conn.execute("""
            INSERT INTO performance_snapshots (
                snapshot_date, campaign_id, ad_id, brand, campaign_type,
                spend_usd, revenue_usd, roas, ncpa, impressions,
                clicks, ctr, frequency, conversions
            ) VALUES (
                :snapshot_date, :campaign_id, :ad_id, :brand, :campaign_type,
                :spend_usd, :revenue_usd, :roas, :ncpa, :impressions,
                :clicks, :ctr, :frequency, :conversions
            )
        """, snapshot)


def upsert_daily_campaign_snapshot(snapshot: dict) -> None:
    """
    Write (or overwrite) a campaign-level daily performance row (ad_id=None).
    Safe to call multiple times — replaces the existing row for that date/campaign.
    Ad-level rows from the pause auditor are separate (they have ad_id set).
    """
    snapshot.setdefault("snapshot_date", datetime.now(timezone.utc).strftime("%Y-%m-%d"))
    snapshot["ad_id"] = None
    with get_db() as conn:
        conn.execute(
            "DELETE FROM performance_snapshots "
            "WHERE snapshot_date=:snapshot_date AND campaign_id=:campaign_id AND ad_id IS NULL",
            snapshot,
        )
        conn.execute("""
            INSERT INTO performance_snapshots (
                snapshot_date, campaign_id, ad_id, brand, campaign_type,
                spend_usd, revenue_usd, roas, ncpa, impressions,
                clicks, ctr, frequency, conversions
            ) VALUES (
                :snapshot_date, :campaign_id, :ad_id, :brand, :campaign_type,
                :spend_usd, :revenue_usd, :roas, :ncpa, :impressions,
                :clicks, :ctr, :frequency, :conversions
            )
        """, snapshot)


def get_performance_window(brand: str, days: int, campaign_type: str | None = None) -> list[dict]:
    query = """
        SELECT * FROM performance_snapshots
        WHERE brand=?
          AND snapshot_date >= date('now', ?)
    """
    params: list = [brand, f"-{days} days"]
    if campaign_type:
        query += " AND campaign_type=?"
        params.append(campaign_type)
    with get_db() as conn:
        rows = conn.execute(query, params).fetchall()
    return [dict(r) for r in rows]


def get_yesterday_summary() -> list[dict]:
    with get_db() as conn:
        rows = conn.execute("""
            SELECT brand, campaign_type,
                   SUM(spend_usd) as spend_usd,
                   AVG(roas) as roas,
                   AVG(ncpa) as ncpa,
                   SUM(impressions) as impressions,
                   SUM(clicks) as clicks,
                   AVG(ctr) as ctr
            FROM performance_snapshots
            WHERE snapshot_date = date('now', '-1 day')
              AND campaign_id IS NOT NULL
              AND ad_id IS NULL
            GROUP BY brand, campaign_type
        """).fetchall()
    return [dict(r) for r in rows]


def get_baseline_summary(days: int = 7) -> list[dict]:
    with get_db() as conn:
        rows = conn.execute(f"""
            SELECT brand, campaign_type,
                   AVG(spend_usd) as avg_spend,
                   AVG(roas) as avg_roas,
                   AVG(ncpa) as avg_ncpa
            FROM performance_snapshots
            WHERE snapshot_date BETWEEN date('now', '-{days} days') AND date('now', '-1 day')
              AND campaign_id IS NOT NULL
              AND ad_id IS NULL
            GROUP BY brand, campaign_type
        """).fetchall()
    return [dict(r) for r in rows]


# ── graduation_log helpers ────────────────────────────────────────────────────

def log_graduation(record: dict) -> None:
    record.setdefault("graduation_date", datetime.now(timezone.utc).isoformat())
    if isinstance(record.get("scale_campaigns"), list):
        record["scale_campaigns"] = json.dumps(record["scale_campaigns"])
    with get_db() as conn:
        conn.execute("""
            INSERT INTO graduation_log (
                meta_creative_id, brand, graduation_date,
                spend_at_graduation, roas_at_graduation, scale_campaigns
            ) VALUES (
                :meta_creative_id, :brand, :graduation_date,
                :spend_at_graduation, :roas_at_graduation, :scale_campaigns
            )
        """, record)


def get_graduated_creative_ids(brand: str) -> set:
    with get_db() as conn:
        rows = conn.execute(
            "SELECT meta_creative_id FROM graduation_log WHERE brand=?", (brand,)
        ).fetchall()
    return {r["meta_creative_id"] for r in rows}


# ── keyword_performance helpers ───────────────────────────────────────────────

def upsert_keyword_performance(record: dict) -> None:
    record.setdefault("snapshot_date", datetime.now(timezone.utc).strftime("%Y-%m-%d"))
    with get_db() as conn:
        conn.execute("""
            INSERT INTO keyword_performance (
                snapshot_date, brand, keyword, impressions,
                clicks, conversions, spend_usd, revenue_usd, roas
            ) VALUES (
                :snapshot_date, :brand, :keyword, :impressions,
                :clicks, :conversions, :spend_usd, :revenue_usd, :roas
            )
            ON CONFLICT(snapshot_date, brand, keyword) DO UPDATE SET
                impressions  = excluded.impressions,
                clicks       = excluded.clicks,
                conversions  = excluded.conversions,
                spend_usd    = excluded.spend_usd,
                revenue_usd  = excluded.revenue_usd,
                roas         = excluded.roas
        """, record)


# ── daypart_log helpers ───────────────────────────────────────────────────────

def upsert_daypart(record: dict) -> None:
    with get_db() as conn:
        conn.execute("""
            INSERT INTO daypart_log (log_date, hour_utc, brand, spend_usd, revenue_usd)
            VALUES (:log_date, :hour_utc, :brand, :spend_usd, :revenue_usd)
            ON CONFLICT(log_date, hour_utc, brand) DO UPDATE SET
                spend_usd   = excluded.spend_usd,
                revenue_usd = excluded.revenue_usd
        """, record)


def get_daypart_data(brand: str, rolling_days: int = 14) -> list[dict]:
    with get_db() as conn:
        rows = conn.execute("""
            SELECT hour_utc,
                   AVG(spend_usd) as avg_spend,
                   AVG(revenue_usd) as avg_revenue,
                   CASE WHEN AVG(spend_usd) > 0
                        THEN AVG(revenue_usd) / AVG(spend_usd)
                        ELSE 0 END as avg_roas,
                   COUNT(*) as data_points
            FROM daypart_log
            WHERE brand=?
              AND log_date >= date('now', ?)
            GROUP BY hour_utc
            ORDER BY hour_utc
        """, (brand, f"-{rolling_days} days")).fetchall()
    return [dict(r) for r in rows]


# ── agent_action_log helpers ──────────────────────────────────────────────────

def log_agent_action(record: dict) -> int:
    """Log a tool execution. Returns the inserted row id."""
    record.setdefault("timestamp", datetime.now(timezone.utc).isoformat())
    if isinstance(record.get("params_json"), dict):
        record["params_json"] = json.dumps(record["params_json"])
    with get_db() as conn:
        cursor = conn.execute("""
            INSERT INTO agent_action_log (
                timestamp, tool_name, brand, params_json,
                rationale, predicted_outcome, executed, source
            ) VALUES (
                :timestamp, :tool_name, :brand, :params_json,
                :rationale, :predicted_outcome, :executed, :source
            )
        """, record)
        return cursor.lastrowid


def get_unmeasured_actions(min_age_days: int = 14) -> list[dict]:
    """Return executed actions old enough to have measurable outcomes."""
    with get_db() as conn:
        rows = conn.execute("""
            SELECT * FROM agent_action_log
            WHERE executed = 1
              AND measured = 0
              AND timestamp <= datetime('now', ?)
            ORDER BY timestamp ASC
        """, (f"-{min_age_days} days",)).fetchall()
    return [dict(r) for r in rows]


def update_action_outcome(action_id: int, actual_outcome: str) -> None:
    """Write the actual outcome and mark action as measured."""
    with get_db() as conn:
        conn.execute(
            "UPDATE agent_action_log SET actual_outcome=?, measured=1 WHERE id=?",
            (actual_outcome, action_id),
        )


# ── calibration_findings helpers ──────────────────────────────────────────────

def insert_calibration_finding(finding: dict) -> None:
    if isinstance(finding.get("action_ids"), list):
        finding["action_ids"] = json.dumps(finding["action_ids"])
    with get_db() as conn:
        conn.execute("""
            INSERT INTO calibration_findings (
                finding_type, pattern, evidence, recommended_correction, action_ids
            ) VALUES (
                :finding_type, :pattern, :evidence, :recommended_correction, :action_ids
            )
        """, finding)


# ── hypothesis_backlog helpers ────────────────────────────────────────────────

def insert_hypothesis(record: dict) -> int:
    if isinstance(record.get("setup_json"), dict):
        record["setup_json"] = json.dumps(record["setup_json"])
    with get_db() as conn:
        cursor = conn.execute("""
            INSERT INTO hypothesis_backlog (
                hypothesis, rationale, experiment_type, brand,
                campaign_type, setup_json, proposed_by
            ) VALUES (
                :hypothesis, :rationale, :experiment_type, :brand,
                :campaign_type, :setup_json, :proposed_by
            )
        """, record)
        return cursor.lastrowid


# ── campaign_log extra helpers ────────────────────────────────────────────────

def get_campaigns(brand: str | None = None, statuses: tuple = ("active",)) -> list[dict]:
    """Fetch campaigns filtered by one or more statuses."""
    placeholders = ",".join("?" for _ in statuses)
    params: list = list(statuses)
    query = f"SELECT * FROM campaign_log WHERE status IN ({placeholders})"
    if brand:
        query += " AND brand=?"
        params.append(brand)
    with get_db() as conn:
        rows = conn.execute(query, params).fetchall()
    return [dict(r) for r in rows]


def resume_campaign(campaign_id: str) -> None:
    with get_db() as conn:
        conn.execute(
            "UPDATE campaign_log SET status='active' WHERE campaign_id=?",
            (campaign_id,),
        )


# ── northbeam_daily_stats helpers ─────────────────────────────────────────────

def upsert_northbeam_stat(record: dict) -> None:
    """Insert or overwrite a single day's Northbeam row for one entity."""
    record.setdefault("entity_name", None)
    with get_db() as conn:
        conn.execute("""
            INSERT INTO northbeam_daily_stats
                (stat_date, level, entity_id, entity_name, spend, revenue, roas, conversions, new_visitors)
            VALUES
                (:stat_date, :level, :entity_id, :entity_name, :spend, :revenue, :roas, :conversions, :new_visitors)
            ON CONFLICT(stat_date, level, entity_id) DO UPDATE SET
                entity_name  = excluded.entity_name,
                spend        = excluded.spend,
                revenue      = excluded.revenue,
                roas         = excluded.roas,
                conversions  = excluded.conversions,
                new_visitors = excluded.new_visitors
        """, record)


def get_northbeam_account_window(days: int) -> dict:
    """
    Account-level Northbeam totals for the last N days (all campaigns summed).
    Returns {spend, revenue, roas, conversions, new_visitors, days_of_data}.
    """
    with get_db() as conn:
        row = conn.execute("""
            SELECT
                SUM(spend)        as spend,
                SUM(revenue)      as revenue,
                CASE WHEN SUM(spend) > 0 THEN SUM(revenue)/SUM(spend) ELSE 0 END as roas,
                SUM(conversions)  as conversions,
                SUM(new_visitors) as new_visitors,
                COUNT(DISTINCT stat_date) as days_of_data
            FROM northbeam_daily_stats
            WHERE level = 'campaign'
              AND stat_date BETWEEN date('now', ?) AND date('now', '-1 day')
        """, (f"-{days} days",)).fetchone()
    return dict(row) if row else {}


def get_northbeam_campaign_window(days: int) -> list[dict]:
    """Per-campaign Northbeam totals for the last N days, ordered by spend desc."""
    with get_db() as conn:
        rows = conn.execute("""
            SELECT
                entity_id,
                entity_name,
                SUM(spend)        as spend,
                SUM(revenue)      as revenue,
                CASE WHEN SUM(spend) > 0 THEN SUM(revenue)/SUM(spend) ELSE 0 END as roas,
                SUM(conversions)  as conversions,
                SUM(new_visitors) as new_visitors,
                COUNT(DISTINCT stat_date) as days_of_data
            FROM northbeam_daily_stats
            WHERE level = 'campaign'
              AND stat_date BETWEEN date('now', ?) AND date('now', '-1 day')
            GROUP BY entity_id, entity_name
            ORDER BY spend DESC
        """, (f"-{days} days",)).fetchall()
    return [dict(r) for r in rows]


def get_northbeam_ad_window(days: int, limit: int = 25) -> list[dict]:
    """Per-ad Northbeam totals for the last N days, top N by spend."""
    with get_db() as conn:
        rows = conn.execute("""
            SELECT
                entity_id,
                entity_name,
                SUM(spend)        as spend,
                SUM(revenue)      as revenue,
                CASE WHEN SUM(spend) > 0 THEN SUM(revenue)/SUM(spend) ELSE 0 END as roas,
                SUM(conversions)  as conversions,
                SUM(new_visitors) as new_visitors,
                COUNT(DISTINCT stat_date) as days_of_data
            FROM northbeam_daily_stats
            WHERE level = 'ad'
              AND stat_date BETWEEN date('now', ?) AND date('now', '-1 day')
            GROUP BY entity_id, entity_name
            ORDER BY spend DESC
            LIMIT ?
        """, (f"-{days} days", limit)).fetchall()
    return [dict(r) for r in rows]


# ── pinterest_ad_group_stats helpers ──────────────────────────────────────────

def upsert_ad_group_stat(record: dict) -> None:
    """Insert or overwrite a single day's Pinterest ad group metrics."""
    with get_db() as conn:
        conn.execute("""
            INSERT INTO pinterest_ad_group_stats
                (stat_date, ad_group_id, ad_group_name, campaign_id, brand, campaign_type,
                 spend_usd, revenue_usd, roas, impressions, clicks, ctr, conversions, ncpa)
            VALUES
                (:stat_date, :ad_group_id, :ad_group_name, :campaign_id, :brand, :campaign_type,
                 :spend_usd, :revenue_usd, :roas, :impressions, :clicks, :ctr, :conversions, :ncpa)
            ON CONFLICT(stat_date, ad_group_id) DO UPDATE SET
                ad_group_name = excluded.ad_group_name,
                spend_usd     = excluded.spend_usd,
                revenue_usd   = excluded.revenue_usd,
                roas          = excluded.roas,
                impressions   = excluded.impressions,
                clicks        = excluded.clicks,
                ctr           = excluded.ctr,
                conversions   = excluded.conversions,
                ncpa          = excluded.ncpa
        """, record)


def get_ad_group_window(days: int) -> list[dict]:
    """Per-ad-group Pinterest totals for the last N days, ordered by spend desc."""
    with get_db() as conn:
        rows = conn.execute("""
            SELECT
                ad_group_id,
                ad_group_name,
                campaign_id,
                brand,
                campaign_type,
                SUM(spend_usd)    as spend_usd,
                SUM(revenue_usd)  as revenue_usd,
                CASE WHEN SUM(spend_usd) > 0 THEN SUM(revenue_usd)/SUM(spend_usd) ELSE 0 END as roas,
                SUM(impressions)  as impressions,
                SUM(clicks)       as clicks,
                CASE WHEN SUM(impressions) > 0 THEN SUM(clicks)*1.0/SUM(impressions) ELSE 0 END as ctr,
                SUM(conversions)  as conversions,
                COUNT(DISTINCT stat_date) as days_of_data
            FROM pinterest_ad_group_stats
            WHERE stat_date BETWEEN date('now', ?) AND date('now', '-1 day')
            GROUP BY ad_group_id, ad_group_name, campaign_id, brand, campaign_type
            ORDER BY spend_usd DESC
        """, (f"-{days} days",)).fetchall()
    return [dict(r) for r in rows]
