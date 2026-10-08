"""Amazon Ads API behavior that the specs do not state, found by calling the live API.

Served to Claude by the MCP server (``api_notes`` tool and ``amazon-ads://notes``
resource) and reproduced in ``docs/guide/quirks.md``, so a new session starts knowing
them instead of rediscovering them.
"""

API_NOTES = """\
# Amazon Ads API: behavior the specs do not spell out

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
- An SP campaign budget must be sent as an object, {"budget": 20.0, "budgetType":
  "DAILY"}. A bare number is answered with a 400 for the whole request that names no
  field, not a per-item 207 error. The two products disagree here: an SB campaign
  (/sb/v4/campaigns) stores the same budget as a plain number beside a sibling
  budgetType, and a number is what it wants. This client widens a number to the object
  for SP campaigns and leaves SB campaign budgets alone.
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
- Sponsored Brands reports keep 60 days for every report type; an older start date
  answers 400 "must be equal to or after report type data retention start date".

## Sponsored Brands ids
- The campaign id in an advertising console URL (a string starting with "A") is not the
  API campaign id; the SB v4 list endpoints answer 400 "Id filter has invalid value".
  List without a filter and match on name, or read campaignId from a report.

## Sponsored Brands keywords and campaigns
- Keywords and negative keywords are still on the v3 API: GET lists with comma-delimited
  filters and startIndex/count paging (count=500 is accepted), integer ids, lower-case
  states. The v3.2 list media types (application/vnd.sbkeyword.v3.2+json,
  application/vnd.sbnegativekeyword.v3.2+json) work. This client's sb.keywords and
  sb.negative_keywords handle all of that and take plans like Sponsored Products.
- A campaign's top-of-search premium is readable and writable only through
  bidding.bidAdjustmentsByPlacement on /sb/v4/campaigns. The advertising console's
  campaign settings page does not show it.
- A campaign read returns four bidAdjustmentsByPlacement entries (DETAIL_PAGE,
  TOP_OF_SEARCH, HOME, OTHER) plus bidOptimizationStrategy, while the v4 spec caps an
  update request at three placements. Amazon accepts the object exactly as it was read,
  with all four, and applies the changed percentage; a read-modify-write of bidding works.

## Sponsored Brands creates that the specs do not warn about
- POST /sb/keywords and POST /sb/negativeKeywords reject state: INVALID_ARGUMENT "The
  noted field is not allowed with this API endpoint : state", even though a read returns
  it and an update accepts it. A created keyword is enabled. This client drops the field.
- A KDP author account cannot create a goal based campaign through the API:
  INVALID_ARGUMENT AUTHOR_CREATED_GOAL_CAMPAIGNS_DISABLED, "Author not authorized to
  create a goal based campaign". Omit goal and costType. Amazon then fills in goal
  PAGE_VISIT, costType CPC and kpi CLICKS itself, which is what a campaign made in the
  advertising console carries, so nothing is lost by leaving them out.
- For the manualCollection ad format the landing page nests inside creative on create,
  while a read returns landingPage at the ad level. Sending it at the ad level on create
  is rejected.

## Sponsored Brands ad groups and ads
- A campaign on its own never serves. A Sponsored Brands campaign needs an ad group under
  it and an ad under that, and the API accepts the campaign without either, so a campaign
  created on its own looks healthy in a list and delivers nothing.
- There is no POST /sb/v4/ads. Each ad format has its own create endpoint
  (/sb/v4/ads/productCollection, /sb/v4/ads/storeSpotlight, /sb/v4/ads/video and so on),
  while listing, updating and archiving share /sb/v4/ads. This client's sb.ads takes an
  adFormat field, posts to that format's endpoint and does not send adFormat in the body.
- An ad group takes no bid. The bid lives on the campaign's keywords, so an ad group is
  only a name, a state and its campaign id.
- The creative and landing page belong to the ad, not the campaign, so changing which
  products an ad shows is an ad update and never a campaign update.

## Profiles and regions
- Profiles live on three regional hosts (NA, EU, FE). Australia and Japan are FE; the UK
  and India are EU. One consent through amazon.com can cover all three.
- KDP author accounts are type "vendor", subType "KDP_AUTHOR".
- Login with Amazon refresh tokens issued since mid 2026 expire 365 days after consent,
  and refreshing does not extend them. Resetting the client secret revokes all of them.
"""
