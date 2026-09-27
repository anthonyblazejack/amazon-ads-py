# Change plans and rollback

A `ChangePlan` describes a set of writes before they happen: for every entity, the
values it holds now and the values the plan sets. It gives you:

- **Review.** A before and after table to read (or show someone) before anything is sent.
- **Rollback.** A plan that puts things back, saved as JSON. That file is the rollback
  token.
- **Integrity.** A fingerprint of the plan's content, so what gets applied is exactly
  what was reviewed.

## Build, review, apply

```python
plan = us.sp.keywords.plan_update(
    [{"keywordId": "111", "bid": 0.50}, {"keywordId": "222", "state": "PAUSED"}],
    note="Raise the exact bid, pause the loser",
)
print(plan.to_markdown())
```

```
| op | kind | id | label | field | before | after |
|---|---|---|---|---|---|---|
| update | sp.keywords | 111 | mystery novels [EXACT] | bid | 0.35 | 0.5 |
| update | sp.keywords | 222 | cozy mystery books [PHRASE] | state | ENABLED | PAUSED |
```

`plan_update` reads the current values, drops changes that would not change anything,
and lists ids Amazon does not have in `plan.missing`.

```python
path = plan.save("plans/")              # plans/plan-<id>.json
result = plan.apply(us, check_drift=True)
result.summary()                        # "update sp.keywords: 2 succeeded, 0 failed"
rollback = result.rollback_plan()
rollback.save("plans/")
```

- `check_drift=True` re-reads every entity first and raises `PlanDriftError` if any
  value has changed since the plan was built. A plan reviewed an hour ago cannot quietly
  overwrite a change made in between.
- `apply` refuses a plan built for a different profile.
- `ChangePlan.load()` refuses a file whose content no longer matches its saved
  fingerprint.

## Rollback

`PlanResult.rollback_plan()` reverses exactly what Amazon accepted:

- updates that succeeded go back to their before values,
- creates that succeeded are archived by the ids Amazon assigned,
- failed items are left out, because they changed nothing,
- archives cannot be reversed and are listed in `warnings`.

`ChangePlan.inverse()` gives the reverse of a plan before applying it. It only covers
updates.

A field with no previous value cannot be restored by an update. A keyword with no bid
of its own, for example, uses the ad group's default bid. Such fields are skipped and
listed in `warnings`, because Amazon does not document what an explicit `null` does.

## Other operations

```python
us.sp.negative_keywords.plan_create([{...}, {...}])   # rollback archives what was made
us.sp.keywords.plan_archive(["111"])                   # permanent; no rollback
```

## Restoring from Amazon's change history

With no saved plan, Amazon's own change log (90 days) can still rebuild one:

```python
from datetime import datetime, UTC

plan = us.history.rollback_plan(datetime(2026, 9, 24, 18, tzinfo=UTC))
print(plan.to_markdown())      # every bid and state changed since then, set back
plan.apply(us, check_drift=True)
```

Entities changed several times go back to their value before the first change. Changes
that cannot be restored with an update (names, budgets, dates) are listed in
`warnings`.

## From the command line

```bash
adsctl plan update keywords bids.csv -m US --note "why"   # CSV: keywordId,bid
adsctl plan show plan-3f9c2a1b7d4e.json
adsctl plan apply plan-3f9c2a1b7d4e.json                   # asks, then saves the rollback
adsctl plan apply plan-3f9c2a1b7d4e.rollback.json          # undo
adsctl plan from-history -m US --since 2026-09-24T18:00
```

## Plan file format

```json
{
  "format": 1,
  "id": "3f9c2a1b7d4e",
  "created_at": "2026-09-26T20:14:03+00:00",
  "profile_id": 1234567890123456,
  "country_code": "US",
  "note": "Raise the exact bid",
  "changes": [
    {"kind": "sp.keywords", "op": "update", "id": "111",
     "before": {"bid": 0.35}, "after": {"bid": 0.5}, "label": "mystery novels [EXACT]"}
  ],
  "missing": [],
  "warnings": [],
  "reverses": null,
  "fingerprint": "6bcbd4b9..."
}
```

`kind` is one of `sp.campaigns`, `sp.ad_groups`, `sp.keywords`, `sp.targets`,
`sp.negative_keywords`, `sp.negative_targets`, `sp.campaign_negative_keywords`,
`sp.campaign_negative_targets`, `sp.product_ads`, `portfolios`. `op` is `update`,
`create` or `archive`.
