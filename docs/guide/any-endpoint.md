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

## Read-only operations

`op.read_only` decides three things: whether `call` retries the operation after a 5xx
or a timeout, whether `adsctl ops call` asks before sending it, and whether the MCP
server's `call_operation` will run it at all. A write that timed out may still have
gone through, so only read-only operations are retried.

It is true for every GET, and for a POST when either of these holds and no operation id
or path segment starts with `create`, `update`, `delete` or `archive`:

- the operation id, or a whole path segment, starts with `list`, `query`, `get`,
  `search`, `describe`, `history` or `recommendation` (`listCampaigns`,
  `/adsApi/v1/query/targets`, `/sp/targets/bid/recommendations`, `/mcp/get_workflow`);
- the path ends in `/list`, `/query`, `/history`, `/recommendations`,
  `/targets/categories`, `/targets/products/count` or `/budgetUsage`.

A word in the middle of a segment does not count, so `POST /sp/targets` is a write even
though "targets" contains "get". PUT, PATCH and DELETE are never read-only.

The rule reads names, not behavior, so it can be wrong both ways. A few reads with
unusual names count as writes, for example `dsp-quick-actions:batchGetExecutionsV1` and
`mcp-eligibility:product_list`; send those with `call` directly or with
`adsctl ops call --yes`. In the other direction, `creative-assets:getUploadLocation`
counts as read-only because its id starts with `get`, although it creates an upload
location (a pre-signed URL that expires after 15 minutes). Check `op.read_only` in
Python, or the `read_only` field of the MCP server's `search_operations` results,
before relying on it.

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
