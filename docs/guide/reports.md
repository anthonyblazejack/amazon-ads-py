# Reports

```python
from datetime import date

result = us.reports.run("sp_targeting", date(2026, 8, 1), date(2026, 9, 25))
result.rows          # list of dicts, every chunk combined
result.chunks        # [ReportChunk(start, end, report_id, rows)]
result.to_dataframe()
```

`run` queues every chunk, waits, downloads, unzips and parses:

- **Ranges over 31 days are split** into consecutive chunks. All chunks are queued
  before waiting on any, so Amazon builds them in parallel.
- **Polling backs off** from 5 to 60 seconds and stops at `timeout` (an hour by
  default) with `ReportFailedError`.
- **Duplicates are reused.** Asking again while an identical report is still building
  makes Amazon answer 425 with the existing id; the library carries on with that one.
- **Retention is checked.** A start date past Amazon's retention (about 95 days, 65 for
  search terms) raises a warning, since the oldest days will come back empty.
- **Expired download links are refreshed.** A report's download URL lasts an hour.

## Presets

Every preset was accepted by the live API for a KDP author profile in September 2026.

| Preset | Amazon report | One row per |
|---|---|---|
| `sp_campaigns` | spCampaigns by campaign | campaign, day |
| `sp_placement` | spCampaigns by campaign and placement | campaign, placement, day |
| `sp_ad_groups` | spCampaigns by ad group | ad group, day |
| `sp_targeting` | spTargeting | keyword or product target, day |
| `sp_search_terms` | spSearchTerm | search term, target, day |
| `sp_advertised_products` | spAdvertisedProduct | advertised product, day |
| `sp_purchased_products` | spPurchasedProduct | product bought after a click that was not the one advertised |
| `sb_campaigns` | sbCampaigns by campaign | Sponsored Brands campaign, day |
| `sb_targeting` | sbTargeting | Sponsored Brands keyword or product target, day |
| `sb_search_terms` | sbSearchTerm | search term, Sponsored Brands target, day |
| `sb_purchased_products` | sbPurchasedProduct | product bought after a Sponsored Brands click, with `attributionType` (Promoted or Brand Halo) |

Metrics in every `sp_*` preset: impressions, clicks, cost, purchases and sales at 1, 7 and 14
days, units at 14 days, and Kindle Edition Normalized Pages read and royalties at 14
days. Sponsored Brands has a single 14-day window, so `sb_*` presets carry impressions,
clicks, cost, purchases, sales and units without a suffix; each SB report type accepts a
different subset (only `sb_campaigns` has Kindle page columns). A Sponsored Brands sale
can be any product of the brand, so use `sb_purchased_products` to see what was
actually bought. `amazon_ads.REPORT_PRESETS` has the exact columns.

## Options

```python
us.reports.run("sp_campaigns", start, end, time_unit="SUMMARY")      # totals per chunk
us.reports.run("sp_targeting", start, end, columns=["keywordId", "cost", "clicks"])
us.reports.run("sp_search_terms", start, end,
               filters=[{"field": "keywordType", "values": ["TARGETING_EXPRESSION"]}])

# Anything else: a full Amazon configuration
us.reports.run({
    "adProduct": "SPONSORED_BRANDS",
    "reportTypeId": "sbCampaigns",
    "groupBy": ["campaign"],
    "columns": ["campaignId", "impressions", "clicks", "cost"],
}, start, end)
```

Step by step:

```python
config = us.reports.configuration("sp_placement")
report_id = us.reports.create(config, start, end)
status = us.reports.wait(report_id)
rows = us.reports.download(status)
```

## Reading the numbers correctly

- Amazon **restates conversions** for weeks after a day (up to about 42 days) and
  impressions for a few days. A number pulled today for last week may still change.
- `keywordBid`, `campaignStatus` and `campaignBudgetAmount` are **values at pull time**,
  not on the row's date. Use [change history](change-history.md) for what a bid was
  on a given day.
- In `sp_targeting`, `keywordId` holds the target id for product targets.
- `sp_search_terms` only contains terms with at least one click.

To keep history past retention and track restatements, use the
[warehouse](warehouse.md).
