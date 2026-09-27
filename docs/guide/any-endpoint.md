# Calling any endpoint

The typed layer covers Sponsored Products, bids, history, reports and profiles. For
everything else (Sponsored Brands, Sponsored Display, DSP, AMC, Stores, Posts, Exports,
budget rules, recommendations, billing, and every other API Amazon publishes), use the
operation catalog.

## The catalog

`amazon_ads.catalog` holds every operation from Amazon's 220 published OpenAPI specs:
method, path, parameters, and the versioned media types Amazon expects in
`Content-Type` and `Accept`.

```python
from amazon_ads import catalog

catalog.search("sponsored brands campaigns list")
op = catalog.get("sponsored-brands-v4:ListSponsoredBrandsCampaigns")
op.method, op.path, op.content_type, op.accept, op.read_only
catalog.apis()["sponsored-display"].source      # the spec it came from
```

Ids are `<api>:<operationId>`. A bare operation id works when it is unique across APIs.

## Calling

```python
us.call("sponsored-brands-v4:ListSponsoredBrandsCampaigns", {"maxResults": 100})

us.call("reporting:getAsyncReport", path_params={"reportId": "abc-123"})

us.call("sponsored-display:listCampaigns", query={"stateFilter": "enabled"})
```

`call` fills in the host, auth, profile scope and media types, applies the same retry
and throttling rules as everything else, and returns parsed JSON. For batch writes,
read per-item results with `amazon_ads.batch.parse_multi_status(response, items)`.

To pin a different media type version than the newest the spec lists, pass
`content_type=` and `accept=`. `op.content_types` and `op.accepts` list what the spec
documents.

## From the command line

```bash
adsctl ops search sponsored display targets
adsctl ops show sponsored-display:listTargetingClauses
adsctl ops call sponsored-brands-v4:ListSponsoredBrandsCampaigns -m US --body '{"maxResults": 10}'
adsctl ops call reporting:getAsyncReport -m US -p reportId=abc-123
```

`ops call` asks for confirmation before any operation that can change data.

## Keeping the catalog current

The catalog is generated from Amazon's specs by `scripts/fetch_specs.py` and
`scripts/generate_catalog.py`. A scheduled workflow re-fetches them weekly and opens a
pull request when Amazon changes anything. See [development](../development.md).
