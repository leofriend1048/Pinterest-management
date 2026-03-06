import os
from dotenv import load_dotenv

load_dotenv()

# ── Credentials ──────────────────────────────────────────────────────────────
PINTEREST_ACCESS_TOKEN  = os.environ["PINTEREST_ACCESS_TOKEN"]
PINTEREST_AD_ACCOUNT_ID = os.environ["PINTEREST_AD_ACCOUNT_ID"]

META_ACCESS_TOKEN  = os.environ["META_ACCESS_TOKEN"]
META_AD_ACCOUNT_ID = os.environ["META_AD_ACCOUNT_ID"]

NORTHBEAM_API_KEY = os.environ["NORTHBEAM_API_KEY"]
NORTHBEAM_ORG_ID  = os.environ["NORTHBEAM_ORG_ID"]

ANTHROPIC_API_KEY = os.environ["ANTHROPIC_API_KEY"]

SLACK_BOT_TOKEN = os.environ["SLACK_BOT_TOKEN"]
SLACK_CHANNEL   = os.getenv("SLACK_CHANNEL", "#pinterest-automated")

DB_PATH     = os.getenv("DB_PATH", "pinterest_buyer/state/pambs.db")
LOG_LEVEL   = os.getenv("LOG_LEVEL", "INFO")
SHADOW_MODE = os.getenv("SHADOW_MODE", "false").lower() == "true"

# ── Scheduler ─────────────────────────────────────────────────────────────────
SCHEDULE_TIME      = "03:00"
SLACK_DIGEST_TIME  = "07:00"
SCHEDULE_TIMEZONE  = "America/New_York"

# ── Brands & campaigns ───────────────────────────────────────────────────────
BRANDS = ["michael_todd_beauty", "nasalfresh_md", "spa_sciences"]

CAMPAIGN_TYPES = [
    "discovery",
    "scale_actalike",
    "scale_pplus",
    "scale_broad",
    "scale_keyword",
]

SCALE_CAMPAIGN_TYPES = [
    "scale_actalike",
    "scale_pplus",
    "scale_broad",
    "scale_keyword",
]

# ── Budgets ───────────────────────────────────────────────────────────────────
DISCOVERY_CAMPAIGN_BUDGET_USD = 300
SCALE_CAMPAIGN_BUDGET_USD     = 300

# ── Weekly discovery ──────────────────────────────────────────────────────────
WEEKLY_CAMPAIGN_DAY         = "monday"
META_TOP_N_CREATIVES        = 5
META_CREATIVE_MIN_SPEND_USD = 150
META_CREATIVE_LOOKBACK_DAYS = 7

# ── Scale graduation ──────────────────────────────────────────────────────────
GRADUATION_MIN_SPEND_USD       = 300
GRADUATION_MIN_ROAS_MULTIPLIER = 0.9   # × target ROAS

# ── Budget decision gates ─────────────────────────────────────────────────────
MIN_SPEND_FOR_DECISION = 50.0
MIN_RUNTIME_DAYS       = 3
BUDGET_CHANGES_PER_DAY = 1

# Budget scale thresholds
ROAS_SCALE_UP_STRONG = 1.30   # ≥130% → +25%, cap $500/day
ROAS_SCALE_UP_LIGHT  = 1.10   # 110–129% → +10%, cap $200/day
ROAS_HOLD_LOWER      = 0.90   # 90–109% → hold
ROAS_NUDGE_DOWN      = 0.70   # 70–89% → -15%
ROAS_PAUSE_CAMPAIGN  = 0.70   # <70% → pause + flag

BUDGET_SCALE_UP_STRONG_PCT = 0.25
BUDGET_SCALE_UP_STRONG_CAP = 500.0
BUDGET_SCALE_UP_LIGHT_PCT  = 0.10
BUDGET_SCALE_UP_LIGHT_CAP  = 200.0
BUDGET_NUDGE_DOWN_PCT      = 0.15

# ── Ad-level pause rules ──────────────────────────────────────────────────────
AD_PAUSE_FREQUENCY_THRESHOLD = 3.5
AD_PAUSE_ROAS_FLOOR          = 0.70   # × target
AD_PAUSE_MIN_SPEND_USD       = 150.0
AD_PAUSE_MIN_RUNTIME_DAYS    = 3
AD_PAUSE_DECAY_THRESHOLD     = 0.30   # 30% drop from week-1 ROAS

# ── Creative scoring weights ──────────────────────────────────────────────────
WEIGHT_ROAS      = 0.45
WEIGHT_NCPA      = 0.35
WEIGHT_CTR       = 0.10
WEIGHT_HOOK_RATE = 0.10

SCORING_MIN_SPEND_USD    = 150
SCORING_LOOKBACK_DAYS    = 7
DECISION_LOOKBACK_DAYS   = 14

# ── Brand ROAS targets ────────────────────────────────────────────────────────
TARGET_ROAS = {
    "michael_todd_beauty": 2.8,
    "nasalfresh_md":       3.2,
    "spa_sciences":        2.5,
}

# ── Keyword seeds ─────────────────────────────────────────────────────────────
KEYWORD_SEEDS = {
    "michael_todd_beauty": [
        "dermaplaning", "facial hair removal", "peach fuzz removal",
        "sonic facial cleansing", "face exfoliation", "skin smoothing device",
    ],
    "nasalfresh_md": [
        "nasal rinse", "sinus rinse", "neti pot alternative",
        "nasal irrigation", "sinus congestion relief", "xylitol nasal spray",
    ],
    "spa_sciences": [
        "facial cleansing brush", "microcurrent device", "LED face mask",
        "at-home spa treatment", "skincare device",
    ],
}

# ── Seasonal budget floors (month: multiplier) ────────────────────────────────
SEASONAL_FLOORS = {
    "michael_todd_beauty": {10: 1.2, 11: 1.4, 12: 1.4},
    "nasalfresh_md":       {2: 1.2, 3: 1.3, 4: 1.2},
    "spa_sciences":        {12: 1.3, 1: 1.2},
}

# ── Anomaly thresholds ────────────────────────────────────────────────────────
ANOMALY_SPEND_SPIKE_PCT    = 0.30   # >30% spike/drop
ANOMALY_ROAS_COLLAPSE_PCT  = 0.30
CROSS_BRAND_OUTPERFORM_X   = 1.5    # 1.5× 30-day baseline triggers cross-brand flag
FORMAT_BUDGET_BOOST_PCT    = 0.15   # 15% boost for top format monthly

# ── Dayparting ────────────────────────────────────────────────────────────────
DAYPART_MIN_DATA_POINTS    = 200
DAYPART_ROLLING_DAYS       = 14

# ── Monthly challenger ────────────────────────────────────────────────────────
CHALLENGER_BUDGET_USD      = 300
CHALLENGER_DURATION_DAYS   = 30

# ── Claude model ─────────────────────────────────────────────────────────────
CLAUDE_MODEL = "claude-sonnet-4-6"
