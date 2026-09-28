<!-- Generated from src/amazon_ads/notes.py by scripts/generate_catalog.py. Edit that file, not this one. -->

# API quirks

Behavior of the live Amazon Ads API that its documentation and specs do not state, found by calling it. The library handles each of these; they are listed so you know what it is doing and why. The MCP server serves the same text to Claude as the `api_notes` tool.

## Throttling
- Amazon answers 429 when an account sends too much. This client waits (Retry-After when
  given, otherwise exponential backoff) and pauses all its other requests during the
  wait. Bid recommendations throttle hardest; expect waits of several seconds when
  pricing hundreds of targets.

## Writes
- Batch writes answer 207 Multi-Status and succeed or fail per item, even when every
  item failed. Always read the per-item results (this client's BatchResult). Amazon's
  per-item results are the record of what changed; no read-back is needed.
- Single items inside a batch can be throttled (throttledError) while the rest succeed.
  The client resends those alone. Bid errors carry the allowed lowerLimit and upperLimit.
- Create and update accept at most 1000 items per call; the client chunks for you.
- Archiving (POST .../delete) is permanent: an archived entity cannot be re-enabled.
  Pause instead when the change might need undoing.
- New SP campaigns default to dynamic bids "down only" unless dynamicBidding.strategy is
  sent; send "MANUAL" for fixed bids.
- Negative keywords are updated by keywordId but reported in 207 results as
  negativeKeywordId (campaign negatives: campaignNegativeKeywordId).

## Suggested bids (POST /sp/targets/bid/recommendations)
- One request may not mix keyword and product (ASIN) expressions: Amazon answers 422
  "All targeting expressions should have the same targeting type". Group them.
- Product targets are priced with type PAT_ASIN (media type
  application/vnd.spthemebasedbidrecommendation.v4+json). ASIN_SAME_AS and
  ASIN_EXPANDED_FROM, the types product targets carry on the campaign side, return 422.
  ASIN_EXPANDED_FROM has no bid recommendation equivalent.
- At most 100 expressions per request.
- Some marketplaces return no suggestion for some expressions. Australia returned none
  for product targets in 2026 (US, CA and UK did).
- Seasonal themes (BFCM_HOLIDAY, PRIME_DAY, ...) can come back alongside
  CONVERSION_OPPORTUNITIES with different values.

## Change history (POST /history)
- With no entity ids and no parent campaigns, Amazon returns zero events unless the
  selector asks for the whole advertiser: parents: [{"useProfileIdAdvertiser": true}].
- Per-type "filters" did not filter by change type when tested, and events of other
  entity types came back too; filter the results yourself.
- Values are strings ("0.75"). 90 days of history. Who made a change is not recorded.
  Sponsored Display changes are not included.

## Reports (reporting v3)
- At most 31 days per report; split longer ranges.
- Retention is about 95 days (65 for search terms); older days come back empty.
- Conversions are restated for weeks after the day (up to about 42 days); impressions for
  a few days. Re-pull recent days rather than trusting the first pull.
- keywordBid, campaignStatus and campaignBudgetAmount in a report are values at pull
  time, not on the row's date. Use change history for what a bid was on a past day.
- A duplicate request while the first is still building answers 425 with the existing
  report id in the message; reuse it.
- In spTargeting, keywordId holds the target id for product targets.
- Search term reports only include terms with at least one click.
- Sponsored Brands sales and purchases count any product of the brand bought within 14
  days of a click, not only the products in the ad. sbPurchasedProduct (the console's
  "Attributed Purchases" report) names each product bought; attributionType is
  "Promoted" when it was one of the ad's products and "Brand Halo" otherwise.
- Sponsored Brands report types accept different columns: sbTargeting rejects the Kindle
  page columns, sbSearchTerm also rejects detailPageViews, and SB metrics have no
  1d/7d/14d suffix except in sbPurchasedProduct.

## Sponsored Brands ids
- The campaign id in an advertising console URL (a string starting with "A") is not the
  API campaign id; the SB v4 list endpoints answer 400 "Id filter has invalid value".
  List without a filter and match on name, or read campaignId from a report.

## Profiles and regions
- Profiles live on three regional hosts (NA, EU, FE). Australia and Japan are FE; the UK
  and India are EU. One consent through amazon.com can cover all three.
- KDP author accounts are type "vendor", subType "KDP_AUTHOR".
- Login with Amazon refresh tokens issued since mid 2026 expire 365 days after consent,
  and refreshing does not extend them. Resetting the client secret revokes all of them.
