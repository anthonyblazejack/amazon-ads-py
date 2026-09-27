# Warehouse

Amazon keeps 65 to 95 days of report data and keeps revising recent days. The warehouse
is a SQLite file that holds on to everything:

```python
from amazon_ads import Warehouse

with Warehouse("ads.sqlite") as store:
    for market in ("US", "UK", "CA", "AU"):
        for outcome in store.sync(ads.profile(market)):
            print(outcome)        # US sp_placement: 1438 rows for 2026-06-24..2026-09-25
```

```bash
adsctl sync --db ads.sqlite -m US -m UK -m CA -m AU
```

## How syncing works

- **First run** reaches back as far as Amazon still has (95 days, 65 for search terms)
  and ends yesterday, the last complete day.
- **Later runs** start `restate_days` (42 by default) before the newest synced day, so
  late-attributed orders are picked up.
- **Re-pulled days are replaced wholesale.** If Amazon drops a row on restatement, it
  disappears from the store too. Days older than the re-pull window are never touched,
  so they survive after Amazon deletes them.
- Default reports: `sp_placement`, `sp_targeting`, `sp_search_terms`. Pass any
  [preset](reports.md#presets).

Run it daily (cron, launchd, a scheduled CI job) and the store becomes the long-term
record.

## Querying

Each preset has a view with every column as a real SQL column, so any SQLite reader
(another language, a BI tool, `sqlite3`) can use the data without this library:

```sql
SELECT date, campaignName, placementClassification,
       SUM(impressions) AS impressions, SUM(clicks) AS clicks, SUM(cost) AS cost
FROM v_sp_placement
WHERE country_code = 'US' AND date >= '2026-09-01'
GROUP BY 1, 2, 3
ORDER BY cost DESC;
```

From Python:

```python
store.rows("sp_targeting", country_code="US", start=date(2026, 9, 1))   # dicts
store.query("SELECT ... FROM v_sp_search_terms ...")                     # SELECT only
store.coverage()      # first and last day, row count and last pull per market/report
```

## Schema

| Table | Holds |
|---|---|
| `report_rows` | `profile_id`, `country_code`, `report`, `date`, `row_key`, `data` (JSON), `pulled_at` |
| `sync_runs` | every sync: profile, report, date range, row count, Amazon report ids, time |
| `v_<preset>` | one view per preset over `report_rows` |

`row_key` is a hash of the columns that identify a row (ids, placement, search term),
not names, so renaming a campaign does not create duplicate rows.
