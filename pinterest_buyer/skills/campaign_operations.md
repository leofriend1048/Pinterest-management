---
name: campaign-operations
description: Campaign-level controls for PAMBS — pausing and resuming whole campaigns, consideration campaign retry windows, keyword expansion, and hypothesis proposals. Load when making campaign-level structural decisions or proposing experiments.
---

# Campaign Operations Playbook

## Campaign-Level Pause vs Ad-Level Pause

Use `pause_campaign` (whole campaign) only when the campaign itself is the problem:
- Budget engine has already nudged down and ROAS is still below 70% of target
- Campaign has been running ≥ 3 days and crossed the full pause guard (see budget-optimization skill)

Use `pause_creative` (individual ad) when a specific creative is underperforming inside an otherwise healthy campaign. Prefer surgical ad-level pauses before escalating to full campaign pauses.

## Campaign Resume Rules

### Standard Scale Campaigns (scale_pplus / scale_broad / scale_actalike)
Resume a paused scale campaign when:
- It has been paused for ≥ **2 weeks**, AND
- The brand's other active campaigns show ROAS at or above target for the past 7 days, AND
- The reason for the original pause was seasonal or data-thin (not structural underperformance)

If the campaign was paused for structural underperformance (sustained low ROAS across all windows), use `propose_hypothesis` to design a proper restart test rather than blindly resuming.

**On resume: call `resume_campaign`** with:
- `brand`: brand key
- `campaign_type`: the campaign type being resumed
- `rationale`: state exact days paused, current brand ROAS context, and why conditions have changed
- `predicted_outcome`: expected ROAS trajectory over the next 14 days

## Hypothesis Proposals

Use `propose_hypothesis` for any experiment that requires human approval before execution:

**Always require approval:**
- Landing page swaps (different URL for a campaign)
- Manual bid tests (overriding auto-bid)
- Consideration campaign retry
- Any budget change that would exceed normal tier limits (e.g. a one-time aggressive scale test)
- Creative format tests (static vs video vs carousel head-to-head)

**Required fields in `setup_json`:**
```json
{
  "creatives": ["list of creative IDs or descriptions"],
  "landing_pages": ["URLs if testing"],
  "duration_days": 14,
  "success_metric": "Northbeam ROAS ≥ X× at day 14",
  "baseline": "current 7d ROAS for comparison"
}
```

**Do NOT propose hypotheses that:**
- Duplicate experiments already in the learnings table
- Have been explicitly rejected in past calibration findings
- Require Pinterest API access we don't have (e.g. creative upload — requires Tier 2 API access)

## Keyword Expansion

Use `add_keyword` when search performance data surfaces a high-intent term not already in KEYWORD_SEEDS.

Qualify a keyword for addition when:
- Impressions ≥ **500** in the past 30 days
- ROAS is above brand target, OR conversions ≥ 3 in 30 days
- The keyword is NOT already in the brand's KEYWORD_SEEDS list

**On keyword add: call `add_keyword`** with:
- `brand`: brand key
- `keyword`: exact keyword string (lowercase)
- `rationale`: state impressions, conversions, and ROAS from search data
- `predicted_outcome`: expected incremental reach and conversion volume

**Note:** `add_keyword` currently queues the keyword for a config update rather than writing it live. Always note this in the rationale so the human operator knows to confirm the config change.

## Campaign Structure Reference

| Campaign Type | Purpose | Budget Origin |
|---------------|---------|---------------|
| discovery | Weekly creative test — new Meta creatives | $300/day launch |
| scale_pplus | Performance Plus automated targeting | $300/day launch |
| scale_broad | Broad interest + keyword targeting | $300/day launch |
| scale_actalike | Actalike audience (purchasers 180d seed) | $300/day launch |

Discovery campaigns run weekly (launched Fridays). Scale campaigns are permanent.
The weekly discovery campaign tests fresh creatives for 7 days before graduation eligibility.

## What NOT to Do

- Do not pause a campaign to "reset" the algorithm as a default tactic — this costs learning time.
- Do not resume campaigns paused for structural underperformance without a `propose_hypothesis` for human approval.
- Do not propose hypotheses already present in the learnings table.
- Do not add keywords with < 500 impressions — insufficient statistical signal.
- Do not call `pause_campaign` when `pause_creative` would be sufficient.
