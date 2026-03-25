import os
from dotenv import load_dotenv

load_dotenv()

# ── Credentials ──────────────────────────────────────────────────────────────
PINTEREST_ACCESS_TOKEN  = os.environ["PINTEREST_ACCESS_TOKEN"]
PINTEREST_AD_ACCOUNT_ID = os.environ["PINTEREST_AD_ACCOUNT_ID"]

META_ACCESS_TOKEN  = os.environ["META_ACCESS_TOKEN"]
META_AD_ACCOUNT_ID = os.environ["META_AD_ACCOUNT_ID"]

NORTHBEAM_API_KEY   = os.environ["NORTHBEAM_API_KEY"]
NORTHBEAM_CLIENT_ID = os.environ["NORTHBEAM_CLIENT_ID"]

ANTHROPIC_API_KEY = os.environ["ANTHROPIC_API_KEY"]

# Optional: if set, enables ROAS-optimised bidding on ad groups
PINTEREST_CONVERSION_TAG_ID = os.getenv("PINTEREST_CONVERSION_TAG_ID", "")

SLACK_BOT_TOKEN = os.getenv("SLACK_BOT_TOKEN", "")
SLACK_CHANNEL   = os.getenv("SLACK_CHANNEL", "#pinterest-automated")
SLACK_ENABLED   = os.getenv("SLACK_ENABLED", "true").lower() == "true" and bool(SLACK_BOT_TOKEN)

DB_PATH     = os.getenv("DB_PATH", "pinterest_buyer/state/pambs.db")
LOG_LEVEL   = os.getenv("LOG_LEVEL", "INFO")
SHADOW_MODE = os.getenv("SHADOW_MODE", "false").lower() == "true"

# ── Scheduler ─────────────────────────────────────────────────────────────────
SCHEDULE_TIME      = "03:00"
SLACK_DIGEST_TIME  = "07:00"
SCHEDULE_TIMEZONE  = "America/New_York"

# ── Brands & campaigns ───────────────────────────────────────────────────────
BRANDS = ["michael_todd_beauty"]

CAMPAIGN_TYPES = [
    "discovery",
    "scale_actalike",
    "scale_pplus",
    "scale_broad",
]

SCALE_CAMPAIGN_TYPES = [
    "scale_actalike",
    "scale_pplus",
    "scale_broad",
]

# ── Brand metadata (code used in naming convention, geo, conversion event) ────
# Naming convention:
#   Campaign : {CODE} | {FUNNEL} | {TYPE}       | {AUDIENCE}              | {PERIOD}
#              MTB    | TOF      | Test          | W11-2026                 (discovery)
#              MTB    | TOF      | Scale         | Actalike                | Q1-2026
#              MTB    | TOF      | Scale         | Perf+                   | Q1-2026
#              MTB    | TOF      | Scale         | Broad                   | Q1-2026
#              MTB    | TOF      | Challenger    | Perf+                   | Mar-2026
#
#   Ad set  : {CODE} | {TARGETING_DETAIL}                     | {GEO} | {BID}
#              MTB    | Actalike-Purchasers-180d               | US    | Auto
#              MTB    | Perf+-Automated                        | US    | Auto
#              MTB    | Interest-Beauty+KW-Broad               | US    | Auto
#              MTB    | Perf+-Automated                        | US    | Auto  (discovery)
#
#   Ad      : (same name as the Meta creative — no renaming)
#
BRAND_CONFIG = {
    "michael_todd_beauty": {
        "code":             "MTB",
        "geo":              ["US"],
        "conversion_event": "checkout",
    },
}

# Pinterest Saved Audience IDs for actalike seed (purchasers 180-day).
# MTB audience ID sourced from: ads.pinterest.com/advertiser/549755981346/audience/edit?audienceId=2542622686748
BRAND_AUDIENCE_IDS = {
    "michael_todd_beauty": os.getenv("MTB_PINTEREST_AUDIENCE_ID", "2542622686748"),
}

# ── Budgets ───────────────────────────────────────────────────────────────────
# All campaigns launch at $300/day; PAMBS scales from there based on ROAS.
CAMPAIGN_BUDGETS = {
    "discovery":      300,
    "scale_actalike": 300,
    "scale_pplus":    300,
    "scale_broad":    300,
    "challenger":     300,
}

# Legacy aliases kept for any code that still references these directly
DISCOVERY_CAMPAIGN_BUDGET_USD = CAMPAIGN_BUDGETS["discovery"]
SCALE_CAMPAIGN_BUDGET_USD     = CAMPAIGN_BUDGETS["scale_actalike"]

# ── Default audience targeting ────────────────────────────────────────────────
# Gender: female only
TARGET_GENDER = ["FEMALE"]

# Age: 21+ desired.  Pinterest's minimum bucket granularity is 18-24, so
# including "18-24" is the closest approximation to 21+.
TARGET_AGE_BUCKETS = ["18-24", "25-34", "35-44", "45-49", "50-54", "55-64", "65+"]

# ── Ad creative defaults ──────────────────────────────────────────────────────
AD_CTA = "SHOP_NOW"

# Per-brand pin title & description for PAMBS-created ads.
# (Existing promoted pins keep their original title/description.)
AD_TITLE = {
    "michael_todd_beauty": "Easy at-home dermaplaning",
}

AD_DESCRIPTION = {
    "michael_todd_beauty": (
        "It\u2019s a shame most ladies haven\u2019t discovered the power of peach fuzz removal "
        "when many are unlocking their skin\u2019s true glow with this simple step!\n\n"
        "Instantly reveal smoother, softer, more radiant skin with the Sonicsmooth "
        "2-in-1 dermaplaning system.\n\n"
        "\u2728 Exfoliates away dead and dry skin\n"
        "\u2728 Removes unwanted facial hair\n"
        "\u2728 Will not make your hair grow back thicker or longer\n"
        "\u2728 Safer to use than a $2 drug store razor"
    ),
}

# ── Attribution window ────────────────────────────────────────────────────────
CLICK_WINDOW_DAYS = 7   # 7-day click attribution
VIEW_WINDOW_DAYS  = 1   # 1-day view attribution

# ── Actalike settings ─────────────────────────────────────────────────────────
ACTALIKE_SEED_DAYS        = 180   # purchasers look-back window for seed audience
ACTALIKE_SCALE_SIZE_RANGE = (0.00, 0.02)   # 0–2% for scale campaigns
ACTALIKE_DISC_SIZE_RANGE  = (0.01, 0.03)   # 1–3% for discovery campaigns

# ── Weekly discovery ──────────────────────────────────────────────────────────
WEEKLY_CAMPAIGN_DAY         = "monday"
META_TOP_N_CREATIVES        = 30
META_CREATIVE_MIN_SPEND_USD = 500
META_CREATIVE_LOOKBACK_DAYS = 7

# ── Scale graduation ──────────────────────────────────────────────────────────
GRADUATION_MIN_SPEND_USD       = 300
GRADUATION_MIN_ROAS_MULTIPLIER = 0.9   # × target ROAS

# ── Budget decision gates ─────────────────────────────────────────────────────
MIN_SPEND_FOR_DECISION = 50.0
MIN_RUNTIME_DAYS       = 3
BUDGET_CHANGES_PER_DAY = 1

# Budget scale thresholds (ratios of TARGET_ROAS)
ROAS_SCALE_UP_STRONG = 1.30   # ≥130% → +25%, cap $500/day
ROAS_SCALE_UP_LIGHT  = 1.10   # 110–129% → +10%, cap $200/day
ROAS_HOLD_LOWER      = 0.90   # 90–109% → hold (+ marginal profit check)
ROAS_NUDGE_DOWN      = 0.70   # 70–89% → -15%
ROAS_PAUSE_CAMPAIGN  = 0.70   # <70% → pause + flag

BUDGET_SCALE_UP_STRONG_PCT = 0.25
BUDGET_SCALE_UP_STRONG_CAP = 500.0
BUDGET_SCALE_UP_LIGHT_PCT  = 0.10
BUDGET_SCALE_UP_LIGHT_CAP  = 200.0
BUDGET_NUDGE_DOWN_PCT      = 0.15

# Marginal profit optimization — estimated ROAS compression per 10% spend increase.
# A campaign at 2.88× ROAS scaled 10% is estimated to run at 2.88 × (1 − 0.05) = 2.74×.
# If that still clears TARGET_ROAS and total revenue proxy increases, we scale.
MARGINAL_ROAS_COMPRESSION = 0.05

# ── Ad-level pause rules ──────────────────────────────────────────────────────
AD_PAUSE_ROAS_FLOOR          = 0.70   # × target
AD_PAUSE_MIN_SPEND_USD       = 150.0
AD_PAUSE_MIN_RUNTIME_DAYS    = 3
AD_PAUSE_DECAY_THRESHOLD     = 0.30   # 30% drop from week-1 ROAS

# ── New visitor rate benchmarks by campaign type ──────────────────────────────
# Healthy new visitor % range. Below the floor triggers a P1 ecosystem health flag.
NEW_VISITOR_RATE_BENCHMARKS = {
    "discovery":      {"healthy_min": 0.80, "healthy_max": 0.95, "floor": 0.70},
    "scale_actalike": {"healthy_min": 0.50, "healthy_max": 0.75, "floor": 0.40},
    "scale_pplus":    {"healthy_min": 0.60, "healthy_max": 0.80, "floor": 0.50},
    "scale_broad":    {"healthy_min": 0.70, "healthy_max": 0.85, "floor": 0.60},
}

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
}

# ── Brand landing pages (Northbeam + UTM tracked) ────────────────────────────
# Pinterest dynamic macros:  {campaignid}, {adgroupid}, {adid}, {keyword},
#                            {device}, {campaign_name}, {adgroup_name}
BRAND_LANDING_URLS = {
    "michael_todd_beauty": (
        "https://michaeltoddbeauty.com/pages/sonicsmooth-pro-plus-12"
        "?nbt=nb:pinterest::{campaignid}:{adgroupid}:{adid}"
        "&nb_kwd={keyword}"
        "&nb_device={device}"
        "&nb_platform=pinterest"
        "&nb_pn={product_name}"
        "&nb_ppgid={promoted_product_group_id}"
        "&nb_pid={product_id}"
        "&nb_ppid={product_partition_id}"
        "&utm_medium=paid"
        "&utm_source=pinterest"
        "&utm_campaign={campaign_name}"
        "&utm_term={adgroup_name}"
    ),
}

# ── Keyword seeds ─────────────────────────────────────────────────────────────
KEYWORD_SEEDS = {
    "michael_todd_beauty": [
        # Dermaplaning & facial tools (exact product territory)
        "dermaplaning", "dermaplaning at home", "dermaplaning tool",
        "peach fuzz removal", "facial hair removal", "face shaving for women",
        # Skin care routines (high-intent adjacent)
        "skin care routine", "skincare routine", "glow up skin care",
        "how to get clear skin", "glowing skin tips", "good skin tips",
        "skin care products", "face exfoliation", "exfoliating face",
        # Sonic / facial device territory
        "sonic facial cleansing", "facial cleansing brush", "face cleansing tool",
        "skin smoothing device", "at home facial", "facial treatment at home",
        # Beauty routine entry points
        "grwm skincare", "morning skincare routine", "night skin care routine",
        "pre shower routine", "gua sha routine", "lymph drainage face massage",
        # Broad beauty intent
        "makeup tips", "natural makeup", "clean makeup", "esthetician tips",
        "beauty routine", "self care routine", "spa day at home",
    ],
}

# ── Interest seeds for broad targeting ───────────────────────────────────────
# Replace string values with Pinterest interest taxonomy IDs from:
# Ads Manager → Targeting → Interests → Browse categories
# Leave empty list to skip interest targeting (falls back to keyword-only).
INTEREST_SEEDS = {
    "michael_todd_beauty": [],   # e.g. ["903"(Beauty), "906"(Skin care), ...]
}

# ── Brand campaign keywords (used by seeder to detect brand from campaign name) ─
# Any campaign whose name (lowercased) contains one of these terms is assigned
# to the matching brand.  Order matters — first match wins.
BRAND_CAMPAIGN_KEYWORDS = {
    "michael_todd_beauty": [
        "sonicsmooth", "sonic smooth", "sonic", "beauty", "dermaplaning",
        "petite", "michael todd", "mtb", "kelly", "cheryl", "caroline",
        "nina", "abbie", "sara", "lillian", "oasis", "actalike", "kw only",
        "performance_plus", "performance plus", "retargeting", "prospecting",
        "conversion", "creative", "catalog", "trending", "weekly trends",
    ],
}

# ── Seasonal budget floors (month: multiplier) ────────────────────────────────
SEASONAL_FLOORS = {
    "michael_todd_beauty": {10: 1.2, 11: 1.4, 12: 1.4},
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
CHALLENGER_BUDGET_USD      = CAMPAIGN_BUDGETS["challenger"]
CHALLENGER_DURATION_DAYS   = 30

# ── Claude model ─────────────────────────────────────────────────────────────
CLAUDE_MODEL = "claude-sonnet-4-6"
