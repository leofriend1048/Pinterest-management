# PAMBS Runbook
**Pinterest Automated Media Buying System — v1.2**
Owner: Leo, CMO — Michael Todd Beauty
Last updated: March 2026

---

## What this system does in one sentence

Every night at 3 AM the system pulls performance data, makes budget and creative decisions, and posts a plain-English digest at 7 AM — so Pinterest runs itself between your weekly reviews.

---

## Schedule at a glance

| Time (EST) | What runs |
|---|---|
| 3:00 AM daily | Full run: graduation check → budget decisions → ad pause audit → AI anomaly scan |
| 3:00 AM Monday | Above + weekly discovery campaign launch + opportunity agent |
| 3:00 AM first Monday of month | Above + monthly Performance+ challenger launch |
| 7:00 AM daily | Morning digest posted to `#pinterest-automated` |
| Real-time | Graduation alerts, budget change alerts, pause alerts, P0 anomaly alerts |

---

## The five campaigns per brand

Every brand runs exactly this structure. 15 campaigns total across the account.

```
[Brand] — Weekly Discovery          $300/day  starts here, new creatives weekly
[Brand] — Scale: Actalike           $300/day  Actalike 0–2%
[Brand] — Scale: Performance+       $300/day  Pinterest automated targeting
[Brand] — Scale: Broad/Interest     $300/day  interest + keyword combined
[Brand] — Scale: Keyword            $300/day  search keywords only
```

Scale campaigns start at $300/day and diverge over time. That divergence is signal — it tells you which targeting type Pinterest rewards for each brand.

---

## Rule 1 — Weekly discovery launch (Mondays, 3 AM)

**What the system does:**

1. Calls Meta API, pulls all active/paused ads from the past 7 days with ≥ $500 spend
2. Removes any creative already in the Pinterest account
3. Ranks remaining creatives by spend descending, takes the top 30
4. Downloads each asset from Meta CDN to local storage (Meta CDN is not cross-platform shareable)
5. Uploads to Pinterest, creates one new campaign at $300/day per brand with Actalike 1–3% targeting and interest expansion on
6. Each creative becomes an individual ad within a single ad set

**What it will NOT do:**
- Launch a campaign if no creatives meet the spend threshold
- Re-upload a creative already in the account

**Human checkpoint:** On the very first run (Phase 2 exit), a human approval gate is required before the first discovery campaign goes live.

---

## Rule 2 — Scale graduation (daily, 3 AM)

**The threshold:**
```
spend ≥ $300 AND ROAS ≥ 0.9 × brand target ROAS
```

Brand targets:
| Brand | Target ROAS | Graduation floor |
|---|---|---|
| Michael Todd Beauty | 2.8x | 2.52x |
| NasalFresh MD | 3.2x | 2.88x |
| Spa Sciences | 2.5x | 2.25x |

**What happens when a creative graduates:**
- It gets added simultaneously as a new ad to all four scale campaigns for that brand
- The original discovery ad stays active — nothing gets paused at graduation
- A real-time Slack alert fires with: creative name, spend at graduation, ROAS achieved, which four campaigns it entered
- The event is logged to `graduation_log`

**What it will NOT do:**
- Graduate the same creative twice
- Stagger the promotion — it's all four scale lanes at once or nothing

---

## Rule 3 — Budget decisions (daily, 3 AM, all active campaigns)

### The ROAS signal

The system does not use a single ROAS number. It looks at three windows and weights them:

```
Weighted ROAS = (yesterday ROAS × 0.50) + (3-day ROAS × 0.30) + (7-day ROAS × 0.20)
```

Windows with no data (new campaigns) are excluded and the remaining weights renormalise. ROAS source is Northbeam (Pinterest-attributed) with platform ROAS as fallback.

### The five tiers

| Weighted ROAS vs target | Action | Notes |
|---|---|---|
| ≥ 130% | Scale UP 25%, cap $500/day | Yesterday must also be ≥ 110% or action drops to hold |
| 110–129% | Nudge UP 10%, cap $200/day | — |
| 90–109% | Hold | No change |
| 70–89% | Nudge DOWN 15% | Seasonal floor applies |
| < 70% | Pause + Slack flag | See pause guard below |

### Gates — a decision only fires when ALL of these pass

1. **Spend gate** — yesterday spend ≥ $50
2. **Runtime gate** — campaign launched ≥ 3 days ago
3. **One-change gate** — no other budget change already executed for this campaign today
4. **Seasonal floor** — new budget cannot go below the floor for the current month (see seasonal floors section)

### Scale-up guard

A strong scale-up (≥ 130% tier) will **drop to hold** if yesterday's ROAS is below 110% of target, even if the weighted average qualifies. This prevents scaling a campaign that had a great 7-day period but is actually weakening right now.

### Pause guard

The weighted ROAS being below 70% is necessary but not sufficient to pause. At least **2 of the 3 windows** must individually be below the 70% floor. If only 1 window is confirmed bad, the system nudges down 15% instead of pausing. This prevents a single catastrophic day from killing a campaign that is otherwise healthy.

**Example:**
- Yesterday ROAS: 1.1x (bad) / 3-day ROAS: 2.4x (fine) / 7-day ROAS: 2.6x (fine)
- Weighted = 1.1×0.50 + 2.4×0.30 + 2.6×0.20 = 1.79x — below target
- Only 1 window confirmed bad → nudge down, do not pause

### Seasonal budget floors

Budget cannot be cut below these multipliers × the current daily budget during these windows:

| Brand | Month | Floor multiplier |
|---|---|---|
| Michael Todd Beauty | October | 1.2× |
| Michael Todd Beauty | November | 1.4× |
| Michael Todd Beauty | December | 1.4× |
| NasalFresh MD | February | 1.2× |
| NasalFresh MD | March | 1.3× |
| NasalFresh MD | April | 1.2× |
| Spa Sciences | December | 1.3× |
| Spa Sciences | January | 1.2× |

*These months often show short-term ROAS weakness because consideration windows get longer before conversion registers. The floor prevents the system from cutting during the highest-intent periods.*

---

## Rule 4 — Ad-level pause audit (daily, 3 AM, every active ad in the account)

This runs independently of the budget engine. Budget decisions happen at the campaign level; underperformance handling happens at the ad level. A creative can be paused in one scale lane while staying active in others where it's working.

The auditor pulls 1-day, 3-day, and 7-day analytics for every active ad, then checks two rules in order:

### Rule 4a — Persistent underperformance across all windows

```
ROAS below 0.7× target in ALL THREE windows (1d, 3d, 7d)
AND 7-day spend > $150
AND runtime > 3 days
```

All three windows must be independently below the floor. If only 1d and 3d are bad but the 7-day average is still holding AND 7-day spend exceeds $300, the system logs a monitoring warning and waits — it does not pause yet. This is a yellow-alert state visible in logs only.

### Rule 4b — ROAS decay from first-week baseline

```
7-day ROAS has dropped > 30% from the ad's first-week ROAS
AND ROAS is below floor in BOTH 1-day AND 3-day windows (confirmed, not recovering)
```

This catches creatives that started strong and are now clearly fatiguing. The decay is measured on the 7-day trend, but the 1-day and 3-day windows must confirm the ad isn't recovering before it gets paused.

**On each pause:** real-time Slack alert fires with ad name, campaign, 1d/3d/7d ROAS breakdown, spend to date, and the specific rule that triggered.

---

## Rule 5 — Monthly Performance+ challenger (first Monday of each month, 3 AM)

One additional challenger campaign per brand launches at $300/day using Pinterest's Performance+ targeting with the current top creatives. Runs for 30 days. The opportunity agent compares its efficiency to the standing Performance+ scale campaign on its next weekly run to track whether Pinterest's automated targeting is improving over time.

The challenger is stored in `campaign_log` as `campaign_type = scale_pplus` with `is_challenger = 1`. This flag prevents the graduation engine from adding new creatives to it — graduated creatives only enter permanent scale campaigns, never challengers.

---

## The AI agent layer

Three Claude-powered agents run as part of the 3 AM job. They do not make decisions — they explain what the rule-based engine did and surface things worth human attention.

### Anomaly detection agent (runs nightly)

Receives a structured JSON snapshot of account performance at four timeframes (daily, weekly, monthly, quarterly) across all 15 campaigns. Returns anomalies classified as:

| Priority | Meaning | Response |
|---|---|---|
| P0 | Requires immediate attention — spend spike/collapse, delivery failure, CPM blow-up | Real-time Slack alert |
| P1 | Needs attention within 24h — trend reversal, fatigue inflection, pacing problem | Bundled into morning digest |
| P2 | Watch only — early signals, keyword drift, audience saturation starting | Logged, not alerted |

The agent states for each anomaly: what changed, magnitude, most likely root cause, and a specific recommended action.

### Narrative agent (writes the morning digest)

Posted to `#pinterest-automated` at 7 AM. Structure:

1. **Yesterday's scorecard** — spend, ROAS, NCPA per brand vs. 7-day average
2. **Creatives graduated overnight** — names, spend at graduation, ROAS, which 4 lanes entered
3. **Budget decisions made** — what changed and why, with ROAS basis
4. **Ads paused** — what got paused, 1d/3d/7d breakdown, rule that triggered
5. **Anomalies** — P0 and P1 items only
6. **Top observation** — one insight or opportunity

### Opportunity agent (runs Mondays)

Posted to `#pinterest-automated` at the end of the Monday 3 AM run. Covers:

- Meta creatives with traction not yet on Pinterest
- Scale lane divergence — which targeting type is winning per brand and by how much
- Keyword expansion candidates based on search term performance
- Cross-brand angle opportunities (if one brand outperforms its 30-day baseline by 1.5×+, checks whether a similar angle exists for the other two brands)
- Format-level wins (video vs. static, aspect ratio) worth concentrating budget toward

---

## Slack alert reference

| Alert | Cadence | Triggered by |
|---|---|---|
| Morning digest | 7 AM daily | Narrative agent |
| Creative graduated | Real-time | Graduation engine |
| P0 anomaly | Real-time | Anomaly agent |
| Budget change (↑ or ↓) | Real-time | Budget engine |
| Campaign paused | Real-time | Budget engine |
| Ad paused | Real-time | Ad pause auditor |
| Weekly opportunity update | Monday after 3 AM run | Opportunity agent |

---

## Shadow mode

Before going fully live, `SHADOW_MODE=true` in `.env` makes the system run all logic, log every decision to the audit database, and post a Slack summary of what *would* have happened — without executing any Pinterest API writes. Use this during the Phase 3 and Phase 4 review windows before Leo signs off.

To enable: set `SHADOW_MODE=true` in `.env` and restart.
To disable: set `SHADOW_MODE=false`.

---

## What the system will never do automatically

- Push budget above $500/day on any campaign
- Make more than one budget change per campaign per day
- Pause a campaign based on a single bad day alone (2-of-3 window confirmation required)
- Pause an ad based on a single bad window (2-of-3 or 3-of-3 confirmation required depending on rule)
- Graduate a creative to scale before it hits $300 spend AND 0.9× ROAS threshold
- Cut budget below seasonal floor during high-intent months
- Add keywords — keyword expansion is advisory only, surfaced in the weekly update for manual addition

---

## Override procedures

### Manually force a budget change
Update the campaign budget directly in Pinterest Ads Manager. The system reads current budget from the Pinterest API on each run, so it will pick up the manual change and apply its logic from the new baseline the next day.

### Prevent the system from touching a campaign
Pause the campaign in Pinterest. The system only acts on campaigns with `status='active'` in the local database. To resume automated management, reactivate in Pinterest and update the `campaign_log` table:

```sql
UPDATE campaign_log SET status='active' WHERE campaign_id='<id>';
```

### Manually graduate a creative
Run the graduation engine standalone against a specific brand:

```bash
python -c "
from pinterest_buyer.intelligence import graduation_engine
graduation_engine.run_graduation_check('michael_todd_beauty')
"
```

### Run the full daily job immediately (without waiting for 3 AM)
```bash
python -m pinterest_buyer.main --once
```

### Post only the morning digest (without re-running the full job)
```bash
python -m pinterest_buyer.main --digest-only
```

---

## Database tables (what's stored)

| Table | What's in it |
|---|---|
| `creative_mirror` | Every Meta creative the system has seen — Pinterest pin IDs, composite score, graduation status, asset path |
| `campaign_log` | All active campaigns, current budget, brand, type, launch date |
| `decision_audit` | Every budget decision ever made — timestamp, ROAS basis (1d/3d/7d/weighted), prior budget, new budget, full rationale |
| `performance_snapshots` | Daily metrics per campaign and per ad: spend, ROAS, NCPA, frequency, impressions |
| `graduation_log` | Every graduation event — creative, brand, spend and ROAS at graduation, which campaigns entered |
| `keyword_performance` | Keyword-level impressions, clicks, conversions, ROAS per brand, updated daily |
| `daypart_log` | Hourly spend + revenue per brand for the dayparting efficiency tracker |

Database lives at `pinterest_buyer/state/pambs.db`. Back it up before any manual schema changes.

---

## Environment variables required

```
PINTEREST_ACCESS_TOKEN      Pinterest system user token
PINTEREST_AD_ACCOUNT_ID     Pinterest ad account ID

META_ACCESS_TOKEN           Meta long-lived system user token (ads_read scope)
META_AD_ACCOUNT_ID          Meta ad account ID (format: act_123456789)

NORTHBEAM_API_KEY           Northbeam API key (used as HTTP Basic username)
NORTHBEAM_CLIENT_ID         Northbeam client ID (Data-Client-ID header)

ANTHROPIC_API_KEY           Claude API key (for all three agents)

SLACK_BOT_TOKEN             Slack bot token (xoxb-...)
SLACK_CHANNEL               Target channel (default: #pinterest-automated)

PINTEREST_CONVERSION_TAG_ID Pinterest conversion tag ID — enables ROAS-optimised bidding on ad groups (optional)

MTB_PINTEREST_AUDIENCE_ID   Michael Todd Beauty Actalike seed audience ID (default: 2542622686748)
NF_PINTEREST_AUDIENCE_ID    NasalFresh MD Actalike seed audience ID (no default — Actalike lane skipped if blank)
SS_PINTEREST_AUDIENCE_ID    Spa Sciences Actalike seed audience ID (no default — Actalike lane skipped if blank)

SHADOW_MODE                 true = log only, no Pinterest writes (default: false)
LOG_LEVEL                   INFO or DEBUG (default: INFO)
DB_PATH                     Path to SQLite file (default: pinterest_buyer/state/pambs.db)
```

---

## Known Northbeam dependency (confirm before Phase 1)

Three things to confirm with Northbeam CSM before go-live:

1. **Professional plan / API access enabled** — the Data Export API (`POST /v1/exports/data-export`) is a paid add-on. Confirm it's active for the account.
2. **`Data-Client-ID` value** — required header alongside the API key. Find it in the Northbeam dashboard under API settings. Set as `NORTHBEAM_CLIENT_ID` env var.
3. **Attribution model ID** — the system auto-discovers the account's available models via `GET /v1/exports/attribution-models` on first run and prefers a clicks+views model. No manual action needed, but confirm with CSM that a clicks+views model is provisioned.

**Attribution settings locked in:** 1-day window, accrual accounting, clicks+views model. This matches the `1-day clicks + views accrual` setting Leo specified.
