---
name: analysis-and-presentation
description: How to analyse like a data scientist and present like a senior media buyer. Load before any performance review, report, comparison, dashboard, chart or artifact, and whenever the user asks for a visual, a table, a summary to share, or "what should I do".
---

# Analysis and presentation

The job is not to report numbers. It is to find what is true, say what to do
about it, and show it so clearly that nobody has to ask a follow-up.

## Work the problem in this order

1. **Frame it.** What decision is this for? Scale, cut, fix, test, or just
   report? The decision sets the level (account, campaign, ad set, ad) and the
   window.
2. **Get the baseline.** Target from `pinterest_account_context`. Then the same
   metric for the prior period and for the account as a whole. A number with
   nothing beside it is not a finding.
3. **Decompose before concluding.** ROAS = AOV x CVR / CPC, and CPC = CPM / CTR.
   When ROAS moves, find which of CPM, CTR, CVR or AOV moved. That names the
   cause: auction (CPM), creative (CTR, hook rate), landing page or offer (CVR),
   basket (AOV).
4. **Go one level down.** An account average hides the answer. Split by
   campaign, then ad, then segment (`segment_by` age, placement, device) until
   you find where the change lives. Stop when a split no longer explains more.
5. **Check it is real** (next section) before saying it.
6. **Recommend.** Specific entity, specific change, the number behind it, and
   what you expect to happen. Then ask before changing anything.

## Is it real?

Every row from `pinterest_performance` carries `confidence`:

| confidence | purchases | What you may say |
| --- | --- | --- |
| `low` | under 10 | Nothing about ROAS or CPA. Judge on CTR, hook rate and CPM only, or wait. |
| `directional` | 10 to 29 | A lean, stated as a lean. Not enough to kill or scale hard. |
| `solid` | 30 or more | A conclusion. |

- **Comparing two things?** Call `pinterest_compare`. It tests click-through
  rate and conversion rate and tells you whether the gap could be chance. Never
  call a winner from two ROAS numbers alone.
- **Compare like with like.** Same attribution windows, same number of days,
  whole weeks where possible (weekday mix moves results on its own).
- **The last one to three days are incomplete.** Conversions keep arriving
  inside the attribution window. A drop that exists only in the most recent
  days is usually lag. Say so, and check again later before acting.
- **One day is not a trend.** Three days in the same direction is worth a
  mention. Seven is a trend.
- **Extremes regress.** The best ad this week on small spend will usually look
  worse next week. Weight by spend.
- **Small denominators lie.** A ROAS of 5 on $40 is noise. Use `min_spend`.
- **Correlation is not the cause.** If ROAS fell the week budgets rose, say
  both happened; do not assert one caused the other unless the breakdown shows it.

When the data cannot support a conclusion, say exactly that and say what would:
"8 purchases, too few to judge. At the current rate it reaches 30 in about nine
days."

## How to write the answer

- **Answer first.** One sentence with the verdict and the number. Then the
  evidence. Then the recommendation.
- **Every number has a unit, a window and a source.** "$4,926 yesterday,
  Pinterest-attributed, 7-day click."
- **Compare, do not just state.** "ROAS 1.9, target 2.8, down from 2.3 the
  week before."
- **Name things.** The campaign, the ad, the id when a change will follow.
- **Be direct about bad news.** If the account is losing money, the first
  sentence says so.
- **Keep it short.** Three findings beat ten. Cut anything that would not
  change a decision.

## Tables

Use a table whenever there are three or more rows of the same shape.

- One row per entity, sorted by the metric that matters (usually spend, or the
  metric in question), biggest first.
- Columns in this order: name, spend, ROAS, purchases, CPA, then diagnostics
  (CTR, CPM, hook rate), then status.
- Money as `$12,345` (cents only under $100). Ratios as `2.56x`. Rates as
  `1.39%`. Counts with thousands separators. Right-align numbers.
- At most about eight columns and fifteen rows in chat. More than that belongs
  in a dashboard.
- Put a total or account row last when it helps the comparison.
- Mark rows with low confidence instead of hiding them.

## Visuals

**The fast path: `pinterest_dashboard`.** It builds a complete, polished,
interactive report in one call: headline ROAS against target, KPI tiles with
change versus the prior period, daily spend and ROAS charts, sortable campaign
and creative tables with status, and spend by age. Use it when the user asks
for a report, a dashboard, a visual, a recap, or something to share. Do the
analysis first and pass your `headline` and `insights` so the page opens with
the conclusion, not just the data.

**A custom visual**, when the question is narrower than a full report and the
app can render one (an artifact in Claude, or an HTML file):

| The question | The form |
| --- | --- |
| One number and whether it is good | A stat tile: value, change, target. Not a chart. |
| How something moved over time | A line. One measure per chart. |
| Which is biggest | Horizontal bars, sorted, one colour. |
| Two measures with different scales | Two charts side by side. Never two y-axes on one chart. |
| A split of one whole into a few parts | One segmented bar. Not a pie. |
| Many rows, many columns | A table. |

Rules that keep a chart honest and readable:

- Bars start at zero. One colour per series, and the same thing keeps the same
  colour everywhere on the page.
- Show the target as a labelled line on any ROAS or CPA chart.
- Label the end of a line and the extremes, not every point.
- Title says what it is; subtitle says the window and the attribution source.
- Status uses a word and an icon as well as a colour: on target, below, low data.
- Thin marks, quiet gridlines, no decoration. Light and dark both work.
- Every chart has the same numbers available as a table.

## What a strong review looks like

The example below is invented.

> **Pinterest missed target last week: ROAS 1.9 against 2.8, on $21.4K.**
>
> | Campaign | Spend | ROAS | Purchases | CPA | CTR | Confidence |
> | --- | --: | --: | --: | --: | --: | --- |
> | Prospecting, video | $9,820 | 1.6x | 181 | $54 | 1.48% | solid |
> | Scale, Performance+ | $7,410 | 2.4x | 205 | $36 | 1.52% | solid |
> | Test, new hooks | $4,170 | 1.7x | 82 | $51 | 1.11% | solid |
>
> **Why:** CTR and CPM are steady in the two larger campaigns; conversion rate
> fell from 3.1% to 2.2% in Prospecting only. The traffic is arriving and not
> buying there, which points at that campaign's landing page, not the creative.
>
> **Do:** move $1,500 a day from Prospecting to Scale today, and test the
> previous landing page against the current one in Prospecting. Want me to make
> the budget change?

It leads with the verdict, shows the evidence in one table, names the cause
from the decomposition, and ends with a specific action and a question.
