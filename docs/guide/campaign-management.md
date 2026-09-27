# Campaign management (Sponsored Products)

Every Sponsored Products entity has the same interface:

| Resource | Model | Id field |
|---|---|---|
| `sp.campaigns` | `Campaign` | `campaignId` |
| `sp.ad_groups` | `AdGroup` | `adGroupId` |
| `sp.keywords` | `Keyword` | `keywordId` |
| `sp.targets` | `Target` | `targetId` |
| `sp.negative_keywords` | `NegativeKeyword` | `keywordId` |
| `sp.negative_targets` | `NegativeTarget` | `targetId` |
| `sp.campaign_negative_keywords` | `CampaignNegativeKeyword` | `keywordId` |
| `sp.campaign_negative_targets` | `CampaignNegativeTarget` | `targetId` |
| `sp.product_ads` | `ProductAd` | `adId` |
| `portfolios` | `Portfolio` | `portfolioId` |

## Reading

```python
us.sp.keywords.list()                                   # enabled and paused
us.sp.keywords.list(states=None)                        # every state, archived too
us.sp.keywords.list(states=["ENABLED"], campaign_ids=["123"], ad_group_ids=["456"])
us.sp.campaigns.list(name="Brand - Exact")                # exact name
us.sp.keywords.get("123456789012345")                   # one, any state, or None
us.sp.targets.get_many(["1", "2"])                      # {id: Target}
for kw in us.sp.keywords.iter():                        # lazy, page by page
    ...
us.sp.keywords.list(filters={"matchTypeFilter": ["EXACT"]})   # any other Amazon filter
```

Pagination follows `nextToken` for you. Id filters longer than Amazon's 1,000-id limit
are split into several requests.

Models accept both spellings and keep unknown fields:

```python
kw = us.sp.keywords.list()[0]
kw.keyword_text, kw.match_type, kw.bid     # snake_case attributes
kw.to_api()                                # Amazon's JSON, camelCase, no None fields
```

`Target.asin` gives the ASIN of a single-ASIN product target.

## Writing

```python
result = us.sp.keywords.create([
    {"campaignId": "1", "adGroupId": "2", "keywordText": "mystery novels",
     "matchType": "EXACT", "bid": 0.45, "state": "ENABLED"},
])
result = us.sp.keywords.update([{"keywordId": "9", "bid": 0.50}])
result = us.sp.keywords.pause(["9", "10"])
result = us.sp.keywords.enable(["9"])
result = us.sp.keywords.delete(["9"])        # archive: permanent, see below
```

Inputs can be dicts in camelCase or snake_case, or models (`Keyword(...)`).

- **Chunking.** Lists over 1,000 items are sent in several calls. Results come back as
  one `BatchResult` whose indexes point into your original list.
- **Read-only fields are dropped from updates.** A keyword's text or match type cannot
  be changed by an update, so they are removed rather than sent and rejected. Each
  entity's updatable fields come from Amazon's spec.
- **Archiving is permanent.** Amazon cannot re-enable an archived entity. Pause
  instead when you might want it back.
- **New campaigns bid dynamically by default.** Amazon uses "dynamic bids, down only"
  unless you send `"dynamicBidding": {"strategy": "MANUAL"}`.

## Batch results

Amazon answers every batch write with 207 and a verdict per item. Nothing raises
because some items failed; you decide:

```python
result = us.sp.keywords.update(changes)
result.ok                      # False if any item failed
result.successes               # [BatchSuccess(index, id, item, entity)]
result.errors                  # [BatchError(index, item, code, message, raw)]
result.ids                     # ids of the successes
result.summary()               # "48 succeeded, 2 failed"
result.raise_for_errors()      # PartialFailureError(result) if anything failed
```

`BatchError.message` is Amazon's reason pulled out of its nested error objects, for
example `"Bid must be at least 0.02"`. `item` is what you sent for that entry.

To review changes before sending them, and to be able to undo them, use
[change plans](change-plans.md).
