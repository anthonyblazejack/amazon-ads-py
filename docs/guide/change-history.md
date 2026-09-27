# Change history

`POST /history` records every change to Sponsored Products and Sponsored Brands entities
for 90 days: previous value, new value and timestamp. It does not record who made the
change, and it does not cover Sponsored Display.

```python
from datetime import datetime, timedelta, UTC

events = us.history.list(since=datetime.now(UTC) - timedelta(days=3))
for e in events:
    print(e.timestamp, e.entity_type, e.entity_id, e.change_type,
          e.previous_value, "->", e.new_value, e.metadata)
```

Filters:

```python
us.history.list(
    since=..., until=...,
    entity_types=["KEYWORD", "PRODUCT_TARGETING"],   # also CAMPAIGN, AD_GROUP, AD, NEGATIVE_KEYWORD
    changes=["BID_AMOUNT", "STATUS"],                # also CREATED, BUDGET_AMOUNT, PLACEMENT_GROUP, ...
    entity_ids=["123456789012345"],
    campaign_ids=["987654321098765"],
    newest_first=True,
)
```

Values are strings (`"0.75"`, `"PAUSED"`). `metadata` carries context such as the
keyword text, match type, campaign and ad group.

## What a value was at a moment

Report rows carry bids as of the pull, not as of the row's date. To know what a bid
really was on a past day:

```python
us.history.value_at("KEYWORD", "123456789012345", "BID_AMOUNT",
                    datetime(2026, 9, 20, tzinfo=UTC))   # "0.15", or None if unchanged
```

`None` means no change to that field in the window, so the value then is the value now.

## Restoring a past state

`us.history.rollback_plan(since)` builds a [change plan](change-plans.md) that restores
bids and states to what they were at `since`. See
[restoring from change history](change-plans.md#restoring-from-amazons-change-history).

## What the library handles

- A query with no entity ids and no campaigns returns **nothing** from Amazon unless it
  asks for the whole advertiser (`useProfileIdAdvertiser`). The library adds that.
- Amazon's per-type change filters did not filter reliably when tested, and other
  entity types came back too, so filtering happens client-side.
- Paging through `nextToken`, and splitting id lists into Amazon's groups of 10.
