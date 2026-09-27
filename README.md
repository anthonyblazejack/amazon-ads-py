# amazon-ads-py

A typed Python client, command line tool and MCP server for the
[Amazon Ads API](https://advertising.amazon.com/API/docs/en-us).

- **Every endpoint.** All 1,204 operations in the 220 API specs Amazon publishes can be
  called by id, with the right method, path and versioned media types. Sponsored
  Products, suggested bids, change history, reports and profiles also have typed,
  hand-written methods.
- **Safe batch writes.** Amazon accepts or rejects each item in a batch on its own and
  answers 207 even when everything failed. Every write returns per-item results, and
  nothing is silently dropped.
- **Change plans and rollback.** Build a plan, review a before and after table, apply it,
  and get a rollback plan that undoes exactly what Amazon accepted. You can also restore
  bids and states to any moment in the last 90 days from Amazon's own change history.
- **Throttling handled.** 429s are retried after Amazon's `Retry-After`, and every
  request from the client pauses during the wait. Writes that could duplicate are never
  retried blindly.
- **Reports in one call.** Queue, poll, download and parse, with ranges longer than 31
  days split automatically. Seven Sponsored Products presets have been verified against
  the live API.
- **A local warehouse.** Report history is synced into SQLite, keeping data after Amazon
  deletes it (65 to 95 days) and re-pulling recent days that Amazon still revises.
- **Built for Claude.** An MCP server lets Claude read the account and propose changes.
  It can only apply a change the user has seen and approved.
- **Amazon's undocumented quirks, handled.** For example, suggested bids for ASIN
  targets, history queries that return nothing unless phrased a certain way, and 425
  duplicate reports. See [API quirks](docs/guide/quirks.md).

Not affiliated with or endorsed by Amazon.

## Install

```bash
pip install "amazon-ads-py[cli]"          # library + adsctl
pip install "amazon-ads-py[cli,mcp]"      # plus the MCP server
pip install "amazon-ads-py[all]"          # plus pandas for report DataFrames
```

Python 3.11 or newer. With [uv](https://docs.astral.sh/uv/):
`uv add "amazon-ads-py[cli]"` or `uvx --from "amazon-ads-py[cli]" adsctl --help`.

## Credentials

You need Amazon Ads API access and a Login with Amazon client. The
[getting started guide](docs/getting-started.md) walks through Amazon's onboarding. Put
the credentials in the environment or a `.env` file:

```bash
AMAZON_ADS_CLIENT_ID=amzn1.application-oa2-client.xxxx
AMAZON_ADS_CLIENT_SECRET=xxxx
AMAZON_ADS_REFRESH_TOKEN=Atzr|xxxx
AMAZON_ADS_CONSENT_DATE=2026-09-26   # optional: warns before the 365-day expiry
```

No refresh token yet? Run `adsctl auth url`, open the link, approve, then run
`adsctl auth exchange --write-env .env` and paste the address you were redirected to.

## Python

```python
from datetime import date
from amazon_ads import AmazonAds

ads = AmazonAds.from_env(env_file=".env")
us = ads.profile("US")                     # or "UK", "AU", a profile id...

# Read
campaigns = us.sp.campaigns.list()
keywords = us.sp.keywords.list(campaign_ids=[campaigns[0].campaign_id])

# Suggested bids for existing keywords, keyed by keyword id
for keyword_id, s in us.bids.for_keywords(keywords).items():
    print(keyword_id, s.low, s.median, s.high)

# Write, with per-item results
result = us.sp.keywords.update([{"keywordId": keywords[0].keyword_id, "bid": 0.45}])
result.raise_for_errors()                  # raises PartialFailureError listing failures

# Or review first: a plan shows before/after and can be reversed
plan = us.sp.keywords.plan_update([{"keywordId": keywords[0].keyword_id, "bid": 0.50}])
print(plan.to_markdown())
applied = plan.apply(us, check_drift=True)
applied.rollback_plan().save("rollbacks/")  # the rollback token

# Reports over any range
report = us.reports.run("sp_placement", date(2026, 8, 1), date(2026, 9, 25))
df = report.to_dataframe()

# Anything else in Amazon's specs, by operation id
us.call("sponsored-brands-v4:ListSponsoredBrandsCampaigns", {"maxResults": 10})
```

## Command line

```bash
adsctl profiles
adsctl list keywords -m US --campaign-id 123456789
adsctl bids targets -m UK                         # suggested vs live bid, every ASIN target
adsctl history -m US --since -3 --change BID_AMOUNT
adsctl report sp_search_terms -m US --start -30 --out terms.json
adsctl sync --db ads.sqlite -m US -m UK -m CA -m AU
adsctl plan update keywords bids.csv -m US --note "raise exact bids"
adsctl plan apply plan-3f9c2a1b7d4e.json          # shows the table, asks, saves the rollback
adsctl ops search sponsored brands campaigns
```

Add `--json`, `--format csv` or `--format jsonl` to any read command. Full reference:
[docs/cli.md](docs/cli.md).

## Claude (MCP server)

Claude Code:

```bash
claude mcp add amazon-ads --env AMAZON_ADS_ENV_FILE=/path/to/.env -- amazon-ads-mcp
```

Claude Desktop (`claude_desktop_config.json`):

```json
{
  "mcpServers": {
    "amazon-ads": {
      "command": "amazon-ads-mcp",
      "env": { "AMAZON_ADS_ENV_FILE": "/path/to/.env" }
    }
  }
}
```

Claude can list and search the account, pull suggested bids, change history and
reports, and call any read-only operation. To change something it builds a plan, shows
you the table, and applies it only with that plan's fingerprint after you approve.
`AMAZON_ADS_MCP_READ_ONLY=1` removes the write tools entirely. See
[docs/mcp.md](docs/mcp.md).

## Documentation

- [Getting started](docs/getting-started.md): API access, credentials, first call
- Guides: [authentication and regions](docs/guide/authentication.md),
  [campaign management](docs/guide/campaign-management.md),
  [change plans and rollback](docs/guide/change-plans.md),
  [suggested bids](docs/guide/suggested-bids.md),
  [change history](docs/guide/change-history.md), [reports](docs/guide/reports.md),
  [warehouse](docs/guide/warehouse.md), [any endpoint](docs/guide/any-endpoint.md),
  [errors and throttling](docs/guide/errors-and-throttling.md),
  [API quirks](docs/guide/quirks.md)
- [CLI reference](docs/cli.md) and [MCP server](docs/mcp.md)
- [API coverage](docs/reference/coverage.md): every operation, and which are typed
- [Development](docs/development.md): tests, regenerating from Amazon's specs, releasing
- [Changelog](CHANGELOG.md)

## License

Apache 2.0. See [LICENSE](LICENSE).
