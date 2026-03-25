---
name: growth-marketer-playbook
description: Core growth marketer decision framework for PAMBS. Covers how to diagnose ROAS changes, identify real trends vs noise, form structured hypotheses, use new visitor rate as an ecosystem signal, and optimize for marginal profit. Load when analyzing account-wide trends, forming hypotheses, or evaluating ecosystem health. Always load alongside budget-optimization.
---

# Growth Marketer Decision Framework

This playbook defines how PAMBS reasons about performance data — not just what thresholds to apply, but *why* signals mean what they mean and *how* to form predictions before acting.

---

## P0 / P1 / P2: Priority Framework

Every anomaly, observation, and alert is classified into one of three priority tiers:

| Priority | Meaning | Response window | Examples |
|---|---|---|---|
| **P0** | Act now. Account is bleeding. | Immediate Slack alert | Spend collapsed >30%, ROAS cratered overnight, delivery failure, CPM spike burning budget |
| **P1** | Act within 24 hours. Trend is reversing. | Morning digest + Slack | Efficiency trend reversal, creative fatigue inflecting, budget pacing off-track, scale campaign diverging |
| **P2** | Monitor. Early signal. | Logged only — not alerted | Keyword drift, first signs of audience saturation, a creative beginning to slow, frequency creeping up |

**P2 anomalies are never posted to Slack.** They are logged and surfaced only if they escalate to P1 over subsequent days.

**P0 anomalies fire an immediate Slack alert AND appear in the morning digest.** Do not wait until 7am for a P0.

---

## Leading vs Lagging Indicators

Never wait for ROAS to move before acting. Leading indicators tell you what ROAS is about to do — use them to get ahead of the problem.

| Indicator | Type | Signals |
|---|---|---|
| CTR declining | Leading | Ad is losing relevance; audience is tuning out |
| Hook rate falling | Leading | Creative is not stopping the scroll; fatigue early stage |
| Frequency rising | Leading | Same users seeing the same ad repeatedly; saturation starting |
| CPM rising | Leading | Bid competition increasing or audience narrowing |
| New visitor % declining (prospecting) | Leading | Audience pool shrinking; retargeting pool will dry up in 30–60 days |
| ROAS dropping | Lagging | Efficiency has already declined; confirms what leading indicators showed |
| NCPA rising | Lagging | Conversion cost has already risen; confirms what ROAS showed |
| Revenue declining | Lagging | Output of all upstream problems |

**Rule:** If two or more leading indicators are moving in the same direction for 3+ days, treat it as a P1 signal even if ROAS has not yet moved.

---

## Trend Windows: What Each Timeframe Means

| Window | Meaning | Act on this alone? |
|---|---|---|
| 1 day | Noise until proven otherwise | Never |
| 3 days | Signal — something is directionally happening | Only if P0-magnitude |
| 7 days | Trend — this is the real state of the campaign | Yes, with context |
| 30 days | Pattern — structural performance level | Yes, for strategic decisions |

The weighted ROAS composite (20% 1d / 30% 3d / 50% 7d) reflects this hierarchy. A single bad day moves the needle 20%; a bad week moves it 50%.

**Never form a conclusion from a 1-day window alone.** The only exception is a P0 spend collapse or delivery failure where waiting costs real money.

---

## Cause Diagnosis: Why ROAS Moved

When ROAS drops, identify the cause before choosing the action. The action is different for each root cause.

| Root Cause | Key Signals | Action |
|---|---|---|
| **Creative fatigue** | Hook rate falling, frequency rising, ROAS declining across 3–7 days | Rotate creative; pause fatigued ads |
| **Audience saturation** | New visitor % declining, frequency rising, CPM stable, ROAS declining | Expand seed audience or switch targeting |
| **Algorithm instability** | CPM spiky, delivery erratic, ROAS volatile day-to-day with no clear trend | Wait 2–3 days; do not touch |
| **Seasonal headwind** | All campaigns softening simultaneously; matches prior-year pattern | Hold spend; do not cut |
| **Bid competition** | CPM rising, ROAS falling, spend holding or rising | Adjust bid strategy; consider audience shift |
| **Attribution lag** | Revenue numbers catching up 2–3 days later; Northbeam vs platform diverging | Check accrual window; do not panic |
| **Audience overlap** | Multiple scale campaigns cannibalizing each other; CPMs rising across all types | Audit overlap; consider frequency caps |

**Do not apply one-size-fits-all responses.** Pausing a campaign when the cause is algorithm instability wastes the learning phase and costs recovery time. Waiting when the cause is creative fatigue compounds the loss.

**Confirm the cause using at least two signals before acting.**

---

## New Visitor Rate: Ecosystem Health Signal

New visitor rate (new visitors ÷ total visitors from a campaign) measures whether a campaign is expanding the addressable audience or recycling existing site visitors. It is a **leading indicator of future lower-funnel performance**.

### The Flywheel

```
High new visitor rate on prospecting campaigns
        ↓
Net-new people enter the site visitor pool
        ↓
Retargeting and scale campaigns have more people to convert
        ↓
Lower-funnel ROAS improves
        ↓
Account-level revenue lifts
```

Killing or neglecting top-of-funnel prospecting campaigns to chase short-term efficiency destroys the pipeline that makes scale campaigns work. **Never judge a prospecting campaign on ROAS alone.**

### Benchmarks by Campaign Type

| Campaign Type | Healthy New Visitor % | Below this → P1 signal |
|---|---|---|
| discovery | 80–95% | < 70% |
| scale_actalike | 50–75% | < 40% |
| scale_pplus | 60–80% | < 50% |
| scale_broad | 70–85% | < 60% |

Retargeting campaigns are expected to have low new visitor rates (5–25%) — this is correct behavior, not a problem.

### Prioritization Rule

When allocating budget or deciding which campaigns to protect from cuts, **weight new visitor rate alongside ROAS**:

- A discovery campaign at 85% new visitor rate and 2.2× ROAS is more strategically valuable than a scale campaign at 2.9× ROAS and 30% new visitor rate — because the first is feeding the ecosystem the second depends on.
- **Do not pause a prospecting campaign that is above its new visitor % benchmark solely because ROAS is in the nudge-down zone.** Form a hypothesis first (see below).

### Account Ecosystem Health Alert

If new visitor % across ALL prospecting campaigns drops below benchmark simultaneously for 7+ days, flag as a **P1 ecosystem health alert**:
- The retargeting pool is shrinking
- Lower-funnel ROAS will follow in 30–60 days
- Immediate action: review audience seeds, expand targeting, check frequency caps

---

## Marginal Profit Optimization

The goal is not to maximize ROAS efficiency — it is to maximize total profit dollars. These are different objectives when the marginal return on additional spend is still above the TARGET_ROAS floor.

### The Principle

```
If increasing spend by X% produces more total gross profit dollars
AND the estimated ROAS at the new spend level stays at or above TARGET_ROAS
→ scale, even if the ROAS ratio drops from where it was
```

### How the Marginal Check Works

In the hold zone (90–109% of target), evaluate whether a 10% budget increase would add net profit:

1. Estimate ROAS at higher spend: `estimated_roas = current_roas × (1 − MARGINAL_ROAS_COMPRESSION)`
   - Default compression factor: 5% per 10% spend increase (accounts for audience curve flattening)
2. Check floor: `estimated_roas ≥ TARGET_ROAS`
3. Check profit: `estimated_roas × proposed_budget > current_roas × current_budget`
4. If both pass → nudge up (profit-maximizing scale)

### Why This Matters

The hold zone at exactly 100% of TARGET_ROAS is not a reason to freeze. If a campaign is generating $1,000/day at 2.8× ROAS and a 10% spend increase would produce $1,080/day at 2.66× ROAS — that is $80 more profit per day at the same or better return than the floor. Scale it.

### Rationale Format

When a marginal profit nudge fires, the rationale must state:
- Current weighted ROAS and ratio
- Estimated ROAS at proposed spend
- Comparison of current vs projected profit proxy
- Confirmation that estimated ROAS clears TARGET_ROAS floor

Example: `"1d=2.85x | 3d=2.91x | 7d=2.88x → weighted=2.88x (103% of 2.8x target) — marginal profit positive: est. ROAS at +10% spend = 2.74x > floor; projected revenue proxy $1,097 vs current $985; nudging UP 10% to $330/day."`

---

## Structured Hypothesis Format

Every non-routine action — any experiment, test, or decision that deviates from the standard tier logic — must be expressed as a structured hypothesis before execution. This creates a measurable, reviewable record.

### Template

```
Observation:   [Specific metric and trend — quantified, with timeframe]
               Example: "MTB Actalike ROAS has been 2.1× for 5 days (75% of 2.8× target).
                         New visitor % has dropped from 68% to 41% over 14 days."

Hypothesis:    [The most likely cause, stated as a testable claim]
               Example: "The 180-day purchaser seed audience is saturated.
                         The same users are being retargeted repeatedly."

Prediction:    [Specific, measurable outcome expected if hypothesis is correct]
               Example: "Expanding seed to 365 days will recover ROAS to within 10%
                         of 2.8× target (≥ 2.52×) within 14 days."

Test:          [Specific action to take]
               Example: "Pause Actalike. Launch Actalike-365d at $300/day."

Measure:       [Success metric and timeframe]
               Example: "Northbeam ROAS ≥ 2.52× at day 14."

Kill switch:   [Condition under which to abort the test and what to do instead]
               Example: "If ROAS < 1.8× at day 7 → pause test, resume original Actalike,
                         propose broad interest alternative."
```

### When a Hypothesis Is Required

Always use `propose_hypothesis` (human approval required) for:
- Any creative format or landing page test
- Budget changes exceeding standard tier limits
- Audience strategy changes (new seed, new targeting type)
- Campaign restarts after structural underperformance
- Any action where the predicted outcome is uncertain and the cost of being wrong is >$500 in misspend

### Hypothesis Quality Standards

- Observation must include specific numbers, not vague descriptions
- Prediction must be a specific metric at a specific timeframe — not "should improve"
- Kill switch must be defined before the test starts — not added later
- Do not propose hypotheses already in the learnings table
- Do not propose hypotheses that have been rejected in calibration findings

---

## When to Hold vs When to Act

Default to holding more than you think you should. The cost of a wrong pause (lost learning phase, algorithm reset, recovery time) often exceeds the cost of one more day of underperformance.

### Hold When:

- Only 1 of 3 ROAS windows is bad (the other two are intact)
- The cause is likely algorithm instability (CPM spiky, delivery erratic)
- The campaign has been running < 3 days (insufficient data)
- The campaign has < $50 in yesterday spend (insufficient signal)
- Leading indicators are stable even if lagging ROAS dipped
- A known external factor explains the dip (holiday, sale end, seasonality)

### Act When:

- 2 or more ROAS windows are confirmed bad
- Leading indicators have been declining for 3+ days
- The cause is diagnosable (creative fatigue, audience saturation) — not just noise
- The campaign has spent enough to have statistical confidence ($150+ in the evaluation window)
- Waiting one more day means materially more misspend

### The Pause Cost Principle

Every campaign pause costs at minimum 3–5 days of learning phase on restart. Before pausing, ask:
- What is the estimated misspend per day at current ROAS vs the cost of a learning phase reset?
- Is the cause structural (will continue getting worse) or temporary (likely to self-correct)?
- Has a nudge-down already been applied and failed to improve the signal?

If the answer to any of these favors holding, hold for one more cycle and re-evaluate.

---

## What NOT to Do

- Do not act on a single day's data alone — 1d is 20% of the signal
- Do not pause a campaign to "reset" the algorithm as a default tactic — this is expensive
- Do not treat ROAS efficiency as the only objective — total profit dollars matter more
- Do not judge prospecting campaigns on ROAS without also checking new visitor rate
- Do not form a diagnosis without confirming it with at least two independent signals
- Do not propose an experiment without a defined kill switch and success metric
- Do not scale a campaign in its first 3 days — the algorithm is still learning
- Do not apply campaign-level pauses when ad-level surgical pauses would be sufficient
- Do not ignore P2 signals for more than 7 consecutive days — they escalate
- Do not make budget decisions on campaigns with < $50 yesterday spend
