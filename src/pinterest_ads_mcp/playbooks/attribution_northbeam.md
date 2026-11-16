---
name: attribution-northbeam
description: How to pair this server with the Northbeam MCP when both are connected. Load whenever Northbeam tools are available and the user asks about ROAS, CPA, revenue, what to scale or what to cut, so decisions use Northbeam's attribution with Pinterest's delivery data beside it.
---

# Pinterest + Northbeam

When the Northbeam MCP is connected in the same session, use both. They answer
different questions:

| Question | Source |
| --- | --- |
| Did it make money? ROAS, revenue, CPA, new-customer metrics | **Northbeam** (the decision number) |
| What did the platform deliver? Impressions, CPM, CTR, hook rate, hold rate, frequency | **Pinterest** (this server) |
| What is live, what does it cost per day, what does the creative look like | **Pinterest** |
| Making a change (pause, budget, launch) | **Pinterest** |

If Northbeam tools are not available, say that numbers are Pinterest-attributed
and carry on. Do not ask the user to install anything.

## The recipe

1. Read `pinterest_account_context` for the target and the attribution window
   the team reports on.
2. Get the Northbeam dashboard: `list_dashboards`, pick the one matching the
   brand. If the window or model is unclear, `list_attribution_windows` and
   `list_attribution_models` show what the dashboard supports.
3. Pull Northbeam for the same dates with `query_performance`:
   - `dimension_ids`: `["breakdown:Platform (Northbeam)", "campaignName", "campaignId"]`
   - `dimension_filters`: `[{"dimension_id": "breakdown:Platform (Northbeam)", "values": ["Pinterest"]}]`
   - `metric_ids`: `["spend", "rev", "roas"]` to start. Call `list_metrics` for
     anything else (transactions, new-customer metrics, CAC) rather than
     guessing an id.
   - `level`: `campaign`, `adset` or `ad`
   - `attribution_window`: the team's window from the context (for example `"7"`)
   - Do **not** pass a small `limit` together with the platform filter: the
     limit is applied first and the filter can then match nothing.
4. Pull Pinterest for the same dates with `pinterest_performance` at the
   matching level.
5. Join on the id, never on the name.

## Joining

Northbeam carries Pinterest's own ids, so rows line up exactly:

| Northbeam level | Northbeam id dimension | pinterest_performance level | Field to join on |
| --- | --- | --- | --- |
| `campaign` | `campaignId` | `campaign` | `id` |
| `adset` | `adsetId` | `ad_group` | `id` |
| `ad` | `adId` | `ad` | `id` |

Every `pinterest_performance` row also carries `campaign_id` (and
`ad_group_id` at ad level) for rolling up.

Northbeam rows with an empty id are not paid campaigns: they are organic and
referral traffic ("Referral: pinterest.com", "UTM [Pinterest / organic]") or
clicks whose tracking template was not filled in ("{campaign_name}"). Leave
them out of paid ROAS, and mention the unfilled-template rows if they carry
revenue, because that is a tracking problem worth fixing.

## Presenting it

Show one table with both, Northbeam first:

| Campaign | Spend | NB revenue | NB ROAS | Pinterest ROAS | CTR | Hook rate |

- Lead with the Northbeam number and judge it against the target.
- Label every ROAS with its source. Never average or add the two.
- Report Northbeam values as returned. Do not round them silently; if you round
  for display, say so. Northbeam conversion counts are fractional by design.
- Spend should agree within a few percent. A larger gap means a date or
  timezone mismatch, or a campaign Northbeam has not picked up.
- State the windows on both sides (for example "Northbeam 7-day click,
  Pinterest 30-day click / 1-day view"). Different windows are the usual reason
  the two disagree.

## Reading the gap

- **Pinterest ROAS well above Northbeam**: Pinterest is claiming view-through
  and long-window conversions that other channels also touched. Normal for
  upper-funnel video. Decide on Northbeam.
- **Northbeam above Pinterest**: unusual. Check that the Pinterest conversion
  tag is firing and that the windows are comparable.
- **Northbeam shows spend but no revenue for days**: check for an attribution
  outage before concluding the ads stopped working. Compare Pinterest's own
  checkouts for the same days.
- **Both low**: it is a real performance problem. Go to the budget and creative
  playbooks.

## Then decide

Apply the thresholds in `budget-optimization` and `creative-management` to the
Northbeam ROAS. Use Pinterest's CTR, hook rate and CPM to explain *why* a
campaign or ad is where it is, and Pinterest's tools to act on it.
