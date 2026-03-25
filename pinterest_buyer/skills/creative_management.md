---
name: creative-management
description: Creative lifecycle rules for PAMBS — graduation from discovery to scale, ad-level pause/resume, fatigue detection, and new visitor rate prioritization. Load when evaluating creatives, deciding whether to graduate, pause, or resume ads, or reviewing creative performance signals.
---

# Creative Management Playbook

> For cause diagnosis, leading indicator interpretation, and hypothesis structure — refer to the **growth-marketer-playbook** skill.

---

## Creative Lifecycle

```
Meta (source of truth)
       ↓  [qualify: spend + ROAS]
  Discovery campaign  ←── weekly test (Fridays)
       ↓  [graduate: spend + ROAS + new visitor rate]
  Scale campaigns (scale_pplus / scale_broad / scale_actalike)
       ↓  [fatigue signals]
  Paused ad
       ↓  [retry window passed]
  Resumed ad
```

---

## Graduation: Discovery → Scale

A creative qualifies to graduate when ALL of the following are true:
1. Pinterest discovery spend ≥ **$300**
2. Northbeam ROAS ≥ **0.9× brand target ROAS** (90% of target)
3. Creative has not already been graduated (check `graduation_status`)

When graduation fires, the creative is added to ALL active scale campaigns simultaneously
(scale_pplus, scale_broad, scale_actalike). It is not graduated to individual campaigns selectively.

**On graduation: call `graduate_creative`** with:
- `meta_creative_id`: the Meta creative ID from the creative_mirror table
- `rationale`: state discovery spend, achieved ROAS vs. 90%-of-target threshold, and new visitor % in discovery
- `predicted_outcome`: expected ROAS range in scale campaigns based on discovery performance

### Meta Creative Scoring Weights (for ranking when multiple qualify)

| Signal | Weight |
|--------|--------|
| Northbeam ROAS | 45% |
| Northbeam NCPA | 35% |
| CTR | 10% |
| Hook rate (ThruPlay ÷ Impressions) | 10% |

Use this ranking to prioritise which creatives to graduate first if the action cap is tight.

### New Visitor Rate at Graduation

When multiple creatives qualify to graduate simultaneously, **break ties using new visitor % in discovery**:
- A creative driving 88% new visitors in discovery will expand the scale campaign audience more than one driving 60%
- All else equal, graduate the higher new visitor % creative first
- Note new visitor % in the `rationale` field regardless

---

## Ad-Level Pause Rules

Pause an individual ad (not the whole campaign) when ANY of the following are true:

### Rule 1 — Persistent Underperformance
- ROAS below **70% of brand target** across ALL three windows (1d, 3d, 7d)
- AND spend ≥ **$150**
- AND runtime ≥ **3 days**

### Rule 2 — ROAS Decay
- Ad's 7-day ROAS has dropped ≥ **30%** from its first-week ROAS
- AND confirmed weak in both 1d AND 3d windows (not just the 7d average)
- AND spend ≥ **$150**

### New Visitor Rate Override

Before pausing an ad in a discovery or prospecting campaign, check its new visitor %:
- If the ad is driving new visitor % **above its campaign-type benchmark** (see growth-marketer-playbook), do NOT pause on ROAS alone
- Instead: flag as a P1 observation and `propose_hypothesis` — the ad may be driving ecosystem value that ROAS doesn't capture
- Exception: if ROAS is below 50% of target (extreme underperformance), pause regardless of new visitor rate

**On pause: call `pause_creative`** with:
- `ad_id`: Pinterest ad ID
- `campaign_id`: Pinterest campaign ID
- `rationale`: state which rule triggered, specific metric values across all three windows, and new visitor % if relevant
- `predicted_outcome`: expected effect on campaign-level ROAS after removing this ad

---

## Fatigue Detection Signals

Watch for these combinations — they indicate creative fatigue before ROAS collapses:

| Signal Combination | Interpretation | Action |
|---|---|---|
| Frequency > 3.0 AND CTR declining AND hook rate declining | Advanced fatigue — ROAS will follow | Pause ad, rotate creative |
| Frequency > 2.0 AND ROAS declining 3d | Early fatigue | P1 flag, prepare rotation |
| Hook rate dropped ≥ 25% vs first-week avg | Creative stopping power gone | P1 flag |
| CTR dropped ≥ 30% vs first-week avg AND spend holding | Audience tuning out | P1 flag |

Fatigue is a **leading indicator** — act on these signals before ROAS hits the pause floor. A creative caught early can often be rested and resumed with better results than one paused at the floor.

---

## Ad Resume Rules

Resume a paused ad when conditions have materially changed:
- A minimum **3-week** pause window has passed, AND
- The brand's overall campaign ROAS has recovered above target for the past 7 days

Do NOT resume ads automatically within the first 3 weeks of being paused.

**On resume: call `resume_creative`** with the same fields as pause, plus:
- `rationale`: state days paused, current brand ROAS context, and what has materially changed since the pause

---

## What NOT to Do

- Do not pause an ad with < $150 spend — insufficient signal
- Do not graduate a creative with spend < $300 on Pinterest, regardless of ROAS
- Do not graduate a creative already marked `graduated` in creative_mirror
- Do not graduate based on Meta spend/ROAS alone — the spend threshold applies to Pinterest discovery spend
- Do not pause multiple ads in the same campaign simultaneously unless fatigue is campaign-wide; removing too many ads at once collapses delivery
- Do not resume within 3 weeks of pausing — Pinterest's algorithm needs time to re-enter the learning phase
- Do not pause a high new visitor % discovery ad on ROAS alone — check ecosystem value first
- Do not ignore hook rate and frequency declines as "just noise" — they precede ROAS drops by 3–5 days
