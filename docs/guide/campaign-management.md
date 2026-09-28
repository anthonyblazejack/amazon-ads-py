# Campaign management (Sponsored Products and Sponsored Brands)

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

Each `BatchError` has:

| Field | Meaning |
|---|---|
| `item` | What you sent for that entry |
| `error_type` | Amazon's error family: `biddingError`, `rangeError`, `entityStateError`, `duplicateValueError`, `entityNotFoundError`, ... |
| `code` | Amazon's reason, e.g. `BID_OUT_OF_MARKET_PLACE_RANGE`, `TOO_LOW`, `PARENT_ARCHIVED_FORBIDS_UPDATES` |
| `message` | Amazon's message, pulled out of its nested error objects |
| `lower_limit`, `upper_limit` | The allowed range, for bid and range errors |
| `hint` | What to do about it, in plain words |
| `transient` | `True` when Amazon did not act on the item (throttled or internal error) |

**Items rejected as throttled inside a batch are sent again**, alone and with backoff,
so a busy moment does not fail part of a batch. Items that failed with an internal error
are sent again for updates and archives, which are safe to repeat, but not for creates,
because the item may already exist. Everything else (a bid out of range, an archived
parent, a duplicate) would fail the same way again and is returned to you.

To review changes before sending them, and to be able to undo them, use
[change plans](change-plans.md).

## Sponsored Brands

Sponsored Brands campaigns, keywords and negative keywords have the same interface, under
`ProfileClient.sb`:

| Resource | Model | Id field | Amazon API |
|---|---|---|---|
| `sb.campaigns` | `SbCampaign` | `campaignId` | v4 (`/sb/v4/campaigns`) |
| `sb.keywords` | `SbKeyword` | `keywordId` | v3 (`/sb/keywords`) |
| `sb.negative_keywords` | `SbNegativeKeyword` | `keywordId` | v3 (`/sb/negativeKeywords`) |

```python
us.sb.keywords.list(campaign_ids=["303"])               # enabled and paused
us.sb.keywords.update([{"keywordId": "101", "bid": 0.95}])
us.sb.negative_keywords.create([
    {"campaignId": "303", "adGroupId": "202", "keywordText": "free pdf",
     "matchType": "negativeExact"},
])
us.sb.campaigns.update([{"campaignId": "303", "bidding": {
    "bidOptimization": False,
    "bidAdjustmentsByPlacement": [{"placement": "TOP_OF_SEARCH", "percentage": 60}],
}}])
plan = us.sb.keywords.plan_update([{"keywordId": "101", "bid": 0.95}])   # plans work too
```

Differences from Sponsored Products, all handled for you:

- **Keywords are on the older v3 API.** Ids are integers and states and match types are
  lower case (`enabled`, `phrase`); negative match types are `negativeExact` and
  `negativePhrase`. Pass states in either case; they are sent lower case.
- **An update must repeat the keyword's parent ids.** Amazon requires `adGroupId` and
  `campaignId` next to `keywordId`; they are read from the account when you leave them out.
- **Smaller batches.** Keyword writes go 100 at a time, campaign writes 10 at a time.
- **Negative keywords cannot be paused.** They are `enabled` or `archived`; `list()` shows
  enabled ones unless you pass `states=None`.
- **Archiving a keyword is one request per keyword** (`DELETE /sb/keywords/{keywordId}`);
  a keyword Amazon cannot find comes back as a per-item `NOT_FOUND` error, not an exception.
- **Placement premiums live in `bidding`.** Send the whole `bidding` object on a campaign
  update; change plans compare it as one field. The object exactly as a read returns it
  (four placements, although the spec says three) is accepted, so read it, change the
  percentage and send it back.
