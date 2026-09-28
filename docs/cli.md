# adsctl

```bash
pip install "amazon-ads-py[cli]"
adsctl --help
```

## Global options

| Option | Meaning |
|---|---|
| `--env-file PATH` | Dotenv file with credentials. Default: `$AMAZON_ADS_ENV_FILE`, else `./.env` |
| `--format table\|json\|jsonl\|csv` | Output of read commands (default `table`) |
| `--json` | Same as `--format json` |
| `--sandbox` | Use Amazon's sandbox host |

`-m/--market` takes a country code (`US`, `UK`, `GB`, `AU`, ...) or a profile id.
Dates take `YYYY-MM-DD` or `-N` for N days ago.

## auth

```bash
adsctl auth url                         # consent link (needs client id and redirect URI)
adsctl auth exchange --write-env .env   # paste the redirect URL; stores the refresh token
adsctl auth check                       # mint a token, show the refresh token's expiry
```

## profiles

```bash
adsctl profiles
```

## list

```bash
adsctl list ENTITY -m MARKET [--campaign-id ID]... [--ad-group-id ID]... [--id ID]...
                             [--state STATE]... [--all-states]
```

`ENTITY`: `campaigns`, `ad-groups`, `keywords`, `targets`, `negative-keywords`,
`negative-targets`, `campaign-negative-keywords`, `campaign-negative-targets`,
`product-ads`, `portfolios`. Table output shows key columns; `--json` shows every field.

## bids

```bash
adsctl bids keywords -m US [--campaign-id ID]... [--ad-group-id ID]... [--theme THEME]
adsctl bids targets  -m UK
```

Live bid next to Amazon's suggested low, median and high for every enabled keyword or
target.

## history

```bash
adsctl history -m US [--since -7] [--until DATE] [--entity-type KEYWORD]...
                     [--change BID_AMOUNT]... [--id ID]... [--campaign-id ID]...
```

## report

```bash
adsctl report PRESET -m US [--start -7] [--end -1] [--summary] [--out rows.json]
```

`PRESET`: `sp_campaigns`, `sp_placement`, `sp_ad_groups`, `sp_targeting`,
`sp_search_terms`, `sp_advertised_products`, `sp_purchased_products`, `sb_campaigns`,
`sb_targeting`, `sb_search_terms`, `sb_purchased_products`.

## sync

```bash
adsctl sync --db ads.sqlite -m US -m UK [--report sp_placement]... [--restate-days 42]
```

See [warehouse](guide/warehouse.md).

## plan

```bash
adsctl plan update ENTITY CHANGES_FILE -m US [--note TEXT] [--out DIR_OR_FILE]
adsctl plan show PLAN_FILE
adsctl plan apply PLAN_FILE [--no-check-drift] [--yes]
adsctl plan from-history -m US --since 2026-09-24T18:00 [--campaign-id ID]...
```

`CHANGES_FILE` is CSV (a header row with the id column and the fields to change) or a
JSON list of objects:

```csv
keywordId,bid
123456789012345,0.25
123456789012346,0.13
```

`plan apply` prints the table, asks for confirmation, applies, prints the per-item
outcome, and writes `plan-<id>.rollback.json` next to the plan. Apply that file to
undo. The exit code is 2 if any item failed.

## ops

```bash
adsctl ops search WORDS... [--api SLUG]
adsctl ops show OPERATION_ID
adsctl ops call OPERATION_ID -m US [--body JSON|@file.json] [-p name=value]... [-q name=value]... [--yes]
```

`ops call` confirms before any operation that can change data unless `--yes` is given.
