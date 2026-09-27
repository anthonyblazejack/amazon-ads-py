# amazon-ads-py

A typed Python client, command line tool (`adsctl`) and MCP server for the Amazon Ads API.

```python
from amazon_ads import AmazonAds

ads = AmazonAds.from_env(env_file=".env")
us = ads.profile("US")
for kw in us.sp.keywords.list(states=["ENABLED"]):
    print(kw.keyword_text, kw.match_type, kw.bid)
```

## What it gives you

| | |
|---|---|
| **Coverage** | All 1,204 operations in Amazon's 220 published API specs, callable by id ([any endpoint](guide/any-endpoint.md), [coverage](reference/coverage.md)) |
| **Typed layer** | Sponsored Products campaigns, ad groups, keywords, targets, negatives, product ads, portfolios; suggested bids; change history; reports; profiles |
| **Writes** | Per-item results for Amazon's 207 responses, automatic chunking to batch limits, read-only fields stripped from updates ([campaign management](guide/campaign-management.md)) |
| **Plans** | Before/after review, fingerprints, drift checks, rollback plans, restore-from-history ([change plans](guide/change-plans.md)) |
| **Throttling** | Retry-After honored, shared cooldown, no blind retries of writes ([errors and throttling](guide/errors-and-throttling.md)) |
| **Reports** | One call from request to rows, 31-day chunking, verified presets ([reports](guide/reports.md)) |
| **Warehouse** | SQLite history that outlives Amazon's retention and tracks restatements ([warehouse](guide/warehouse.md)) |
| **CLI** | `adsctl` for every read, plans for every write ([CLI](cli.md)) |
| **MCP** | Claude reads freely, and writes only plans a person approved ([MCP server](mcp.md)) |

## Where to start

1. [Getting started](getting-started.md): get API access and credentials, make a first call.
2. [Campaign management](guide/campaign-management.md) for everyday reads and writes.
3. [API quirks](guide/quirks.md): what the live API does that its docs do not say.

## Design choices

- **Policy free.** The library moves data and makes writes safe. It has no opinion on
  what a bid should be.
- **Amazon's names.** Models accept and emit Amazon's camelCase field names as well as
  Python's snake_case, so the API docs apply unchanged.
- **Unknown fields survive.** Models keep fields Amazon adds before the library knows
  about them.
- **Sync, thread-safe.** One client can be shared between threads; token refresh and
  throttling cooldowns are shared.
