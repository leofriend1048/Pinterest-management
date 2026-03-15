---
name: creative-management
description: Creative lifecycle rules for PAMBS — graduation from discovery to scale, ad-level pause/resume, and fatigue detection. Load when evaluating creatives, deciding whether to graduate, pause, or resume ads, or reviewing creative performance signals.
---

# Creative Management Playbook

## Creative Lifecycle

```
Meta (source of truth)
       ↓  [qualify: spend + ROAS]
  Discovery campaign  ←── weekly test (Fridays)
       ↓  [graduate: spend + ROAS]
  Scale campaigns (scale_pplus / scale_broad / scale_actalike)
       ↓  [fatigue signals]
  Paused ad
       ↓  [retry window passed]
  Resumed ad
```

## Graduation: Discovery → Scale

A creative qualifies to graduate when ALL of the following are true:
1. Pinterest discovery spend ≥ **$300**
2. Northbeam ROAS ≥ **0.9× brand target ROAS** (90% of target)
3. Creative has not already been graduated (check `graduation_status`)

When graduation fires, the creative is added to ALL active scale campaigns simultaneously
(scale_pplus, scale_broad, scale_actalike). It is not graduated to individual campaigns selectively.

**On graduation: call `graduate_creative`** with:
- `meta_creative_id`: the Meta creative ID from the creative_mirror table
- `rationale`: state discovery spend and achieved ROAS vs. the 90%-of-target threshold
- `predicted_outcome`: expected ROAS range in scale campaigns based on discovery performance

### Meta Creative Scoring Weights (for ranking when multiple qualify)
| Signal | Weight |
|--------|--------|
| Northbeam ROAS | 45% |
| Northbeam NCPA | 35% |
| CTR | 10% |
| Hook rate (ThruPlay ÷ Impressions) | 10% |

Use this ranking to prioritise which creatives to graduate first if the action cap is tight.

## Ad-Level Pause Rules

Pause an individual ad (not the whole campaign) when ANY of the following are true:

### Fatigue Signal: High Frequency
- Frequency > **3.5** (user has seen the ad more than 3.5 times on average)
- AND spend ≥ **$150**
- AND runtime ≥ **3 days**

### Underperformance Signal: Low ROAS
- ROAS < **70% of brand target** (ad-level, not campaign-level)
- AND spend ≥ **$150**
- AND runtime ≥ **3 days**

### ROAS Decay Signal
- Ad's ROAS in the most recent 7 days has dropped ≥ **30%** vs its first-week ROAS
- AND spend ≥ **$150**

**On pause: call `pause_creative`** with:
- `ad_id`: Pinterest ad ID
- `campaign_id`: Pinterest campaign ID
- `rationale`: state which signal triggered (frequency/low ROAS/decay) and the specific metric value
- `predicted_outcome`: expected effect on campaign-level ROAS and frequency after removing this ad

## Ad Resume Rules

Resume a paused ad when conditions have materially changed:
- Frequency has reset (campaign frequency dropped below 2.5 in the past 7 days)
- OR a minimum **3-week** pause window has passed AND the brand's overall campaign ROAS has recovered above target

Do NOT resume ads automatically within the first 3 weeks of being paused.

**On resume: call `resume_creative`** with the same fields as pause.

## What NOT to Do

- Do not pause an ad with < $150 spend — insufficient signal.
- Do not graduate a creative with spend < $300 on Pinterest, regardless of ROAS.
- Do not graduate a creative already marked `graduated` in creative_mirror.
- Do not graduate based on Meta spend/ROAS alone — the spend threshold applies to Pinterest discovery spend.
- Do not pause multiple ads in the same campaign simultaneously unless fatigue is campaign-wide; removing too many ads at once collapses delivery.
- Do not resume within 3 weeks of pausing — Pinterest's algorithm needs time to re-enter the learning phase.
