---
name: budget-optimization
description: ROAS-based budget scaling rules for PAMBS. Load when evaluating campaign spend, making budget adjustments, or deciding whether to scale up, nudge down, or pause a campaign. Always load growth-marketer-playbook alongside this skill.
---

# Budget Optimization Playbook

> For cause diagnosis, leading indicator interpretation, new visitor rate, hypothesis structure, and hold vs act decisions — refer to the **growth-marketer-playbook** skill. This playbook covers the mechanical rules only.

---

## ROAS Signal: Northbeam Weighted Composite

Never act on a single day's ROAS. Always use the weighted composite across three windows:

| Window | Weight |
|--------|--------|
| Yesterday (1d) | 20% |
| Last 3 days    | 30% |
| Last 7 days    | 50% |

If a window has no data (zero ROAS), exclude it and renormalise the remaining weights.
The 7-day window carries the most weight — it provides a stable signal. Yesterday is a smaller leading indicator only.

---

## Five-Tier Decision Framework

Compare `weighted_roas / target_roas` (the ratio) to these thresholds:

| Tier | Ratio vs Target | Action | Amount |
|------|-----------------|--------|--------|
| 1 | ≥ 130% | Scale UP strong | +25%, capped at $500/day |
| 2 | 110–129% | Nudge UP | +10%, capped at $200/day |
| 3 | 90–109% | Hold (or marginal profit check — see below) | No change unless profit test passes |
| 4 | 70–89% | Nudge DOWN | −15% |
| 5 | < 70% | Pause (with guards) | See below |

---

## Marginal Profit Check (Tier 3 Hold Zone)

A campaign in the hold zone (90–109% of target) should still be evaluated for a profit-maximizing nudge up.

**Run the marginal profit check when ratio is 90–109%:**

1. Estimate ROAS at 10% higher spend: `estimated_roas = weighted_roas × (1 − MARGINAL_ROAS_COMPRESSION)`
   - `MARGINAL_ROAS_COMPRESSION` = 0.05 (5% estimated compression per 10% spend increase)
2. If `estimated_roas ≥ target_roas` AND `estimated_roas × proposed_budget > weighted_roas × current_budget`
3. → Nudge up 10% (profit-maximizing scale) instead of holding

**Rationale must state:**
- All three ROAS windows and weighted average
- Estimated ROAS at proposed spend level
- Revenue proxy comparison (current vs proposed)
- Confirmation that estimated ROAS clears the TARGET_ROAS floor

If the marginal check fails (estimated ROAS would drop below floor, or profit would not increase), hold.

---

## Guards — Read Before Acting

### Scale-Up Strong Guard (Tier 1)
Do NOT scale up 25% if yesterday's ROAS is below 110% of target, even if the weighted average qualifies.
Rationale: prevents scaling on stale multi-day data when yesterday already dipped.

### Pause Guards (Tier 5)
A campaign must satisfy BOTH conditions before pausing:
1. Weighted ROAS < 70% of target
2. At least **2 of 3 windows** individually below 70% of target

If the weighted ROAS is below 70% but fewer than 2 windows are confirmed bad, nudge down 15% instead.

### New Visitor Rate Guard (Prospecting Campaigns)
Before pausing or nudging down a discovery or scale campaign, check new visitor %:
- If new visitor % is above its benchmark (see growth-marketer-playbook), do NOT pause — form a hypothesis instead
- A prospecting campaign feeding new traffic to the site has ecosystem value beyond its ROAS alone
- Preference: `propose_hypothesis` over pause when new visitor % is healthy

### Minimum Gates (applies to ALL tiers)
- Yesterday spend must be ≥ **$50** before any decision fires
- Campaign must have been running for ≥ **3 days**
- Maximum **one budget change per campaign per day**

---

## Seasonal Floors

Budget floors are applied by month. Never cut a budget below its seasonal floor.
Floors are defined as a multiplier on the campaign's base budget:

| Brand | High-floor months |
|-------|------------------|
| Michael Todd Beauty | Oct ×1.2, Nov ×1.4, Dec ×1.4 |

---

## Brand ROAS Targets

TARGET_ROAS is the profitability floor — not just a goal. All budget decisions reference this value.

| Brand | Target ROAS |
|-------|-------------|
| michael_todd_beauty | 2.8× |

---

## Using the adjust_campaign_budget Tool

When calling `adjust_campaign_budget`:
- `new_budget_usd`: Apply the percentage change to the **current** budget, then floor at the seasonal minimum.
- `rationale`: State all three ROAS windows explicitly (e.g. "1d=3.1× | 3d=2.9× | 7d=2.6× → weighted=2.74× vs target=2.8×") and which tier this falls into. If a marginal profit nudge, include the profit proxy comparison.
- `predicted_outcome`: Be specific — name the expected ROAS range or spend trajectory, not vague phrases like "should improve."

---

## What NOT to Do

- Do not act on yesterday's ROAS alone — it is only 20% of the signal
- Do not pause a campaign with < $50 in yesterday spend — insufficient signal
- Do not scale up if yesterday dipped below 110% of target (even if the weighted avg qualifies)
- Do not apply budget changes to campaigns already changed today
- Do not pause a prospecting campaign with healthy new visitor % without first forming a hypothesis
- Do not treat the hold zone as a reason to never scale — run the marginal profit check
