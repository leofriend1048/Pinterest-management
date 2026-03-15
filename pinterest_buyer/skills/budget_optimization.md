---
name: budget-optimization
description: ROAS-based budget scaling rules for PAMBS. Load when evaluating campaign spend, making budget adjustments, or deciding whether to scale up, nudge down, or pause a campaign.
---

# Budget Optimization Playbook

## ROAS Signal: Northbeam Weighted Composite

Never act on a single day's ROAS. Always use the weighted composite across three windows:

| Window | Weight |
|--------|--------|
| Yesterday (1d) | 10% |
| Last 3 days    | 40% |
| Last 7 days    | 50% |

If a window has no data (zero ROAS), exclude it and renormalise the remaining weights.
The heavy 7d weight makes the signal stable. Yesterday is a small leading indicator only.

## Five-Tier Decision Framework

Compare `weighted_roas / target_roas` (the ratio) to these thresholds:

| Tier | Ratio vs Target | Action | Amount |
|------|-----------------|--------|--------|
| 1 | ≥ 130% | Scale UP | +25%, capped at $500/day |
| 2 | 110–129% | Nudge UP | +10%, capped at $200/day |
| 3 | 90–109% | **Hold** | No change |
| 4 | 70–89% | Nudge DOWN | −15% |
| 5 | < 70% | Pause (with guards) | See below |

## Guards — Read Before Acting

### Scale-Up Strong Guard (Tier 1)
Do NOT scale up 25% if yesterday's ROAS is below 110% of target, even if the weighted average qualifies.
Rationale: prevents scaling on stale multi-day data when yesterday already dipped.

### Pause Guards (Tier 5)
A campaign must satisfy ALL three conditions before pausing:
1. Weighted ROAS < 70% of target
2. At least **2 of 3 windows** individually below 70% of target
3. 7-day spend ≥ **$200** (enough data to act on)

If the weighted ROAS is below 70% but the pause guards are not met, nudge down 15% instead.

### Minimum Gates (applies to ALL tiers)
- Yesterday spend must be ≥ **$50** before any decision fires
- Campaign must have been running for ≥ **3 days**
- Maximum **one budget change per campaign per day**

## Seasonal Floors

Budget floors are applied by month. Never cut a budget below its seasonal floor.
Floors are defined as a multiplier on the campaign's base budget:

| Brand | High-floor months |
|-------|------------------|
| Michael Todd Beauty | Oct ×1.2, Nov ×1.4, Dec ×1.4 |
| NasalFresh MD | Feb ×1.2, Mar ×1.3, Apr ×1.2 |
| Spa Sciences | Dec ×1.3, Jan ×1.2 |

## Brand ROAS Targets

| Brand | Target ROAS |
|-------|-------------|
| michael_todd_beauty | 2.8× |
| nasalfresh_md | 3.2× |
| spa_sciences | 2.5× |

## Using the adjust_campaign_budget Tool

When calling `adjust_campaign_budget`:
- `new_budget_usd`: Apply the percentage change to the **current** budget, then floor at the seasonal minimum.
- `rationale`: State all three ROAS windows explicitly (e.g. "1d=3.1× | 3d=2.9× | 7d=2.6× → weighted=2.74× vs target=2.8×") and which tier this falls into.
- `predicted_outcome`: Be specific — name the expected ROAS range or spend trajectory, not vague phrases like "should improve."

## What NOT to Do

- Do not act on yesterday's ROAS alone — it is only 10% of the signal.
- Do not pause a campaign that has < $200 in 7d spend, regardless of ROAS.
- Do not scale up if yesterday dipped below 110% of target (even if the weighted avg qualifies).
- Do not apply budget changes to campaigns already changed today.
- Do not change a budget if yesterday spend was under $50 — there is not enough signal.
