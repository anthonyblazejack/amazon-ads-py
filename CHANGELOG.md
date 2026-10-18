# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project uses
[semantic versioning](https://semver.org/).

## [Unreleased]

### Fixed

- `advertising-billing:getAdvertiserInvoices` (GET /invoices) and
  `advertising-billing:getInvoice` (GET /invoices/{invoiceId}) now send
  `Accept: application/json`. Amazon's spec documents only
  `application/vnd.invoices.v1.1+json` and `application/vnd.invoice.v1.1+json`, and the
  live API answers both with 406 Not Acceptable and an empty body (the list on all 12
  marketplaces tested), so `call()` and `call_operation` could not read invoices at all. The fix is a
  `catalog.CORRECTIONS` entry applied when the catalog loads, not an edit of the
  generated `catalog.json`, so regenerating the catalog from Amazon's specs keeps it.
  `Operation.note` says why an operation differs from its spec, `op.accepts` still lists
  what the spec documents, and the coverage page marks the two operations
  **corrected**. API notes describe the 406 and the rest of what the invoice endpoints
  do that the spec does not say: the `{"status", "payload"}` wrapper, `nextCursor`
  paging that drops the key on the last page, the open billing period listed as
  `ACCUMULATING` with no id, and `POST /invoiceSummaries/list` answering with
  `invoiceSummaries` at the top level where its spec nests them under `aggregation`.
- The `mcp-*` operations are no longer sent. They come from the OpenAPI files in which
  Amazon publishes the tools of its hosted Ads MCP server (beta), and their
  `POST /mcp/<tool>` paths are not routes on the Ads API hosts, which answer them with
  403 "Invalid key=value pair (missing equal-sign) in Authorization header", the same as
  any path they do not serve. That reads like a demand for AWS request signing but is
  not one. 47 of the 110 passed `Operation.read_only`, so `call_operation` let an agent
  spend a call on them. `ProfileClient.call()`, `call_operation` and `adsctl ops call`
  now refuse them before any request with `UnsupportedOperationError`, whose message
  names the Ads API operation to call instead for `mcp-billing:list_invoices` and
  `mcp-billing:list_invoice_summaries`. `call_operation` checks this before its
  write check, so an MCP tool that writes is not pointed at the plan tools.
- `Operation.read_only` no longer calls a write read-only because a read word appears
  inside a path segment. It matched its read words (`list`, `query`, `get`, `search`,
  ...) anywhere inside the last path segment, so "get" inside "targets", "budgetRules"
  and "targetPromotionGroups", and "search" inside "audienceResearch", made 60 Ads API
  writes count as reads, among them
  `sponsored-products:CreateSponsoredProductsTargetingClauses` (`POST /sp/targets`),
  `sponsored-brands-v3:createTargets` and `createNegativeTargets`, the targeting creates
  of the DSP, media planning and unified (`/adsApi/v1/create/targets`) APIs, and the
  budget rule creates and associations for Sponsored Products, Brands and Display.
  Through those, `call_operation` on the MCP server created entities with no plan and no
  approval, `ProfileClient.call()` resent them after a 5xx or a timeout (which can create
  every target twice), and `adsctl ops call` sent them without asking. A read word now
  has to start the operation id or a whole path segment, and an operation id or segment
  starting with `create`, `update`, `delete` or `archive` is never read-only, which also
  catches `rule-based-audiences:createQueryBasedAudience` (`POST /amc/audiences/query`).
  Every path the transport retries as read-only (`READ_ONLY_POST_SUFFIXES`) now counts
  as read-only in the catalog too. Because the read words now match any segment, 28
  reads on the unified `/adsApi/v1/query/...` paths, plus
  `sponsored-brands-v4:SBOptimizationRecommendation` and
  `SBTargetingGetTargetableASINCounts`, become read-only and callable through
  `call_operation`. Three reads that were read-only only by the same substring accident
  now count as writes, so `call_operation` refuses them and `ops call` asks first:
  `dsp-quick-actions:batchGetExecutionsV1`,
  `measurement:CheckDSPAudienceResearchEligibility` and the deprecated
  `sponsored-products-v2:createKeywordBidRecommendations`. One write named like a read is
  still read-only: `creative-assets:getUploadLocation`, which creates an upload URL that
  expires after 15 minutes.
- A Sponsored Products campaign budget given as a number (`{"campaignId": "9", "budget":
  20}`) is now sent as the object Amazon requires, `{"budget": 20.0, "budgetType":
  "DAILY"}`. Amazon rejects a bare number with a 400 for the whole request that names no
  field, and `plan_update` built a plan that looked right, with an object before and a
  number after, so the only clue was the mismatched shapes in the plan table. Budgets are
  now widened and validated through the `Budget` model in `create`, `update`,
  `plan_update` and `plan_create` alike, so a plan's before and after read the same way
  and the plan that is approved is the request that gets sent. `plan_update` and
  `plan_create` refuse a budget that is neither a number, a mapping nor a `Budget` with a
  `ValueError` naming the field, instead of building a plan that fails part way through
  applying. `plan_update` knows the campaign's current values and keeps its existing
  `budgetType`, so raising a lifetime budget no longer plans a switch to daily; `update`
  and `create` read nothing back, so a bare number there means a daily budget, and the
  object has to be passed explicitly to say otherwise. The budget may be given in either
  spelling (`budgetType` or `budget_type`) or as a `Budget` instance. Sponsored
  Brands campaigns are untouched: they store the budget as a plain number beside a
  sibling `budgetType`, and the widening would break them. `EntitySpec.object_fields`
  records, per entity, which fields Amazon takes only as an object.

### Added

- `UnsupportedOperationError`, `Operation.unsupported_reason`, `Operation.note`,
  `Operation.ensure_sendable()`, `catalog.CORRECTIONS`, `catalog.is_hosted_mcp_tool()`,
  `catalog.search(include_unsupported=True)` and `adsctl ops search
  --include-unsupported`. `search_operations` rows carry `note` and `unsupported` when
  they apply.

- API notes now record Amazon's keyword text constraints and the shape of its report
  creation throttle, so `api_notes` and `docs/guide/quirks.md` carry both. The keyword
  limits (80 characters; 10 space-separated parts for a positive keyword or a
  `negativeExact`, 4 for any other negative match type; the hyphen, plus and period only
  inside a word; and a short list of accepted symbols that excludes round brackets and
  the colon) are documented by Amazon but only on a JavaScript-rendered page, and appear
  nowhere in the OpenAPI specs, where `keywordText` is a plain string with no `maxLength`
  and no `pattern`. A client cannot refuse a bad keyword from the spec, so an over-long
  one is only discovered as a per-item 207 error after the rest of the batch has been
  written. Report creation (`POST /reporting/reports`) is throttled per account over a
  window longer than one request's retries can span, so a bulk sync asking for many
  report types across several profiles has its last requests refused while the earlier
  ones succeed; the note says to retry after the run has stopped asking rather than
  inside the loop, and to widen `RetryPolicy` for such a sync.
- `sb.keywords.create` and `sb.negative_keywords.create` drop `state`. Both endpoints
  reject it outright, although a read returns it and an update accepts it, and a created
  keyword is enabled anyway. `SbV3Spec.create_forbidden` holds the fields a create must
  not send.
- Sponsored Brands ad groups and ads: `ProfileClient.sb.ad_groups` and `sb.ads` (v4), so a
  Sponsored Brands campaign can be built end to end rather than only updated. A campaign
  needs an ad group and an ad to serve at all, and the API accepts a campaign without
  either. Ads have no single create endpoint, one per format instead, so `sb.ads` takes an
  `adFormat` field (default `productCollection`, the console's Collections layout), posts
  to that format's endpoint and leaves the field out of the body; one call cannot mix
  formats because Amazon numbers per-item results by their position in the request. The
  MCP server's `list_entities`, `plan_update`, `plan_create` and `plan_archive` accept
  `sb_ad_groups` and `sb_ads`, and `adsctl` accepts `sb-ad-groups` and `sb-ads`.
- Sponsored Brands campaign management: `ProfileClient.sb.campaigns` (v4),
  `sb.keywords` and `sb.negative_keywords` (v3) with the same list, update, create,
  archive and change-plan interface as Sponsored Products. Plans, drift checks and
  rollback plans work for them, so the MCP server's `list_entities`, `plan_update`,
  `plan_create` and `plan_archive` accept `sb_campaigns`, `sb_keywords` and
  `sb_negative_keywords`, and `adsctl list` / `adsctl plan update` accept the `sb-*`
  names. Updates fill in the parent ids SB v3 requires; campaign updates carry placement
  premiums in `bidding`.
- Sponsored Brands report presets: `sb_campaigns`, `sb_targeting`, `sb_search_terms` and
  `sb_purchased_products`, available to `Reports.run`, `adsctl report`, `adsctl sync` and
  the MCP server's `run_report`.
- `BatchError` carries Amazon's error family (`error_type`), the allowed range for bid and
  range errors (`lower_limit`, `upper_limit`), a plain-language `hint`, and `transient`.
- Items rejected as throttled inside a 207 batch are sent again alone with backoff; items
  that hit an internal error are resent for updates and archives but never for creates.
- `apply_plan` in the MCP server reports each failed item with its id, label, what was
  sent, the allowed range and what to do.

### Changed

- `catalog.search()`, `search_operations` and `adsctl ops search` leave out operations
  this client cannot send (today the 110 `mcp-*` tools) unless asked for them or scoped
  to their API with `api=`. A scoped search returns them with the reason instead of an
  empty list. The coverage page counts 1,094 sendable operations in 202 Ads API specs,
  counts the 18 MCP tool specs (110 tools) apart, and marks their rows **MCP tool**.

## [0.1.0] - 2026-09-26

First release.

### Added

- `AmazonAds` and `ProfileClient`: Login with Amazon token caching and refresh, profile
  discovery across the NA, EU and FE regions, market lookup by country code or id.
- Operation catalog generated from all 220 OpenAPI specs Amazon publishes (1,204
  operations), with `ProfileClient.call()` to send any of them with the right method,
  path and versioned media types.
- Typed Sponsored Products v3 resources (campaigns, ad groups, keywords, targets,
  ad group and campaign negatives, product ads) and portfolios: paginated listing,
  create, update, pause, enable and archive, with batch chunking and read-only field
  stripping taken from Amazon's spec.
- `BatchResult` for Amazon's 207 Multi-Status responses, pairing every success and
  error with the item sent.
- Change plans: before/after review, fingerprints, drift checks, rollback plans built
  from what Amazon accepted, and restoring bids and states from change history.
- Suggested bids for keywords, product (ASIN) targets and auto targets, with the
  keyword/ASIN split and `PAT_ASIN` conversion the endpoint needs, and seasonal themes.
- Change history with `value_at()` for what a bid or state was at a past moment.
- Reporting v3 in one call: 31-day chunking, parallel queueing, polling with backoff,
  425 duplicate reuse, retention warnings, and seven Sponsored Products presets
  verified against the live API.
- SQLite warehouse that keeps report history past Amazon's retention and re-pulls the
  42 days Amazon may still restate, with a SQL view per report.
- Throttling: `Retry-After` honored, a cooldown shared across requests, and no retries
  of writes that could duplicate.
- `adsctl` command line tool for auth, profiles, listing, bids, history, reports,
  syncing, plans and any operation.
- `amazon-ads-mcp` MCP server: reads, plans, and fingerprint-gated applies, with a
  read-only mode.
- Documentation site, API quirks page, and coverage page generated from the catalog.
