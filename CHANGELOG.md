# Changelog

All notable changes to this project are documented here. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and the project uses
[semantic versioning](https://semver.org/).

## [Unreleased]

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
