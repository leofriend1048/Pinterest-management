---
name: campaign-operations
description: Campaign-level controls for PAMBS — pausing and resuming whole campaigns, consideration campaign retry windows, keyword expansion, and hypothesis proposals. Load when making campaign-level structural decisions or proposing experiments.
---

# Campaign Operations Playbook

> For cause diagnosis, leading indicator interpretation, and the full hypothesis thinking framework — refer to the **growth-marketer-playbook** skill. This playbook covers the mechanical rules and required tool call formats.

---

## Campaign-Level Pause vs Ad-Level Pause

Use `pause_campaign` (whole campaign) only when the campaign itself is the problem:
- Budget engine has already nudged down and ROAS is still below 70% of target
- Campaign has been running ≥ 3 days and crossed the full pause guard (see budget-optimization skill)

Use `pause_creative` (individual ad) when a specific creative is underperforming inside an otherwise healthy campaign. Prefer surgical ad-level pauses before escalating to full campaign pauses.

---

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

---

## Hypothesis Proposals

Use `propose_hypothesis` for any experiment that requires human approval before execution. Every hypothesis must follow the structured format from the **growth-marketer-playbook**.

**Required fields in `setup_json`:**
```json
{
  "observation": "Specific metric and trend with numbers and timeframe",
  "hypothesis": "Testable causal claim about why this is happening",
  "prediction": "Specific measurable outcome at a specific timeframe",
  "test": "Exact action to take",
  "success_metric": "e.g. Northbeam ROAS ≥ 2.52× at day 14",
  "kill_switch": "Condition to abort + what to do instead",
  "creatives": ["list of creative IDs or descriptions if relevant"],
  "landing_pages": ["URLs if testing landing pages"],
  "duration_days": 14,
  "baseline": "current 7d ROAS and new visitor % for comparison"
}
```

**Always require `propose_hypothesis` (human approval) for:**
- Landing page swaps (different URL for a campaign)
- Manual bid tests (overriding auto-bid)
- Consideration campaign retry
- Any budget change exceeding normal tier limits (e.g. aggressive scale test)
- Creative format tests (static vs video vs carousel head-to-head)
- Audience strategy changes (new seed audience, new targeting type, actalike expansion)
- Campaign restarts after structural underperformance

**Do NOT propose hypotheses that:**
- Duplicate experiments already in the learnings table
- Have been explicitly rejected in past calibration findings
- Require Pinterest API access we don't have (e.g. creative upload — requires Tier 2 API access)
- Lack a defined kill switch — a hypothesis without a kill switch is not a hypothesis, it's a guess

---

## Hypothesis Quality Checklist

Before calling `propose_hypothesis`, confirm:
- [ ] Observation includes specific numbers and a timeframe (not "ROAS is low")
- [ ] Hypothesis identifies a specific causal mechanism (not "performance has declined")
- [ ] Prediction is a specific metric at a specific day (not "should improve")
- [ ] Kill switch defines both the abort condition AND the fallback action
- [ ] The experiment is not already in the learnings table
- [ ] Budget exposure is stated (estimated spend during test period)

---

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

---

## Campaign Structure Reference

| Campaign Type | Purpose | Budget Origin | Expected New Visitor % |
|---|---|---|---|
| discovery | Weekly creative test — new Meta creatives | $300/day launch | 80–95% |
| scale_pplus | Performance Plus automated targeting | $300/day launch | 60–80% |
| scale_broad | Broad interest + keyword targeting | $300/day launch | 70–85% |
| scale_actalike | Actalike audience (purchasers 180d seed) | $300/day launch | 50–75% |

Discovery campaigns run weekly (launched Fridays). Scale campaigns are permanent.
The weekly discovery campaign tests fresh creatives for 7 days before graduation eligibility.

---

## What NOT to Do

- Do not pause a campaign to "reset" the algorithm as a default tactic — this costs learning time
- Do not resume campaigns paused for structural underperformance without a `propose_hypothesis` for human approval
- Do not propose hypotheses already present in the learnings table
- Do not add keywords with < 500 impressions — insufficient statistical signal
- Do not call `pause_campaign` when `pause_creative` would be sufficient
- Do not propose a hypothesis without a kill switch — every test needs an abort condition defined upfront
- Do not resume a scale campaign without checking that other active campaigns provide enough ROAS context
