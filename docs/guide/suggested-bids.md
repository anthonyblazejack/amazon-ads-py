# Suggested bids

Amazon's theme-based bid recommendations, with low, median and high per targeting
expression.

## Existing keywords and targets

```python
keywords = us.sp.keywords.list(states=["ENABLED"], campaign_ids=["123"])
by_id = us.bids.for_keywords(keywords)          # {keyword_id: SuggestedBid}
by_id["123456789012345"].median

targets = us.sp.targets.list(states=["ENABLED"])
result = us.bids.for_targets(targets)
result.suggestions                              # [SuggestedBid], in input order
result.unsupported                              # targets Amazon offers no suggestion for
```

A `SuggestedBid` has `expression_type`, `value`, `low`, `median`, `high`, `theme` and
`priced` (`False` when Amazon returned no range).

## Any expressions in an ad group

```python
from amazon_ads import Expression

us.bids.for_ad_group("campaign-id", "ad-group-id", [
    Expression("KEYWORD_EXACT_MATCH", "mystery novels"),
    Expression("PAT_ASIN", "B0EXAMPLE1"),
    Expression("CLOSE_MATCH"),
])
```

## Before an ad group exists

```python
us.bids.for_new_ad_group(
    asins=["B0MYBOOK01"],
    expressions=[Expression("KEYWORD_PHRASE_MATCH", "cozy mystery")],
    strategy="MANUAL",
    placement_adjustments={"PLACEMENT_TOP": 50},
)
```

## What the library handles

- **Keyword and product expressions are sent separately.** A request that mixes them
  fails as a whole with 422.
- **Product targets are priced as `PAT_ASIN`.** A product target's campaign-side type
  is `ASIN_SAME_AS`, which the recommendation endpoint rejects. The library converts
  (`ASIN_SAME_AS` to `PAT_ASIN`, `ASIN_CATEGORY_SAME_AS` to `PAT_CATEGORY`, the auto
  types to `CLOSE_MATCH`/`LOOSE_MATCH`/`SUBSTITUTES`/`COMPLEMENTS`). `ASIN_EXPANDED_FROM`
  has no equivalent and lands in `unsupported`.
- **At most 100 expressions per request.** Longer lists are split.
- **Unpriced expressions stay visible.** When a marketplace returns nothing for an
  expression (Australia for product targets, in 2026), it comes back with `None`
  values instead of disappearing.
- **Throttling.** This endpoint throttles hardest. Pricing hundreds of targets involves
  429 waits of a few seconds, which the client absorbs.

## Seasonal themes

Next to `CONVERSION_OPPORTUNITIES`, Amazon can return seasonal themes such as
`BFCM_HOLIDAY` or `PRIME_DAY` with different values:

```python
us.bids.for_keywords(keywords, theme="BFCM_HOLIDAY")
us.bids.for_ad_group_all_themes("c", "g", expressions)   # {theme: [SuggestedBid]}
```

## CLI and MCP

```bash
adsctl bids keywords -m US --campaign-id 123
adsctl bids targets -m UK --json
```

MCP: `get_suggested_bids` (existing) and `get_suggested_bids_for_new`.
