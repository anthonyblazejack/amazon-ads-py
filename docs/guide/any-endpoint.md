# Calling any endpoint

The typed layer covers Sponsored Products, bids, history, reports and profiles. For
everything else (Sponsored Brands, Sponsored Display, DSP, AMC, Stores, Posts, Exports,
budget rules, recommendations, billing, and every other API Amazon publishes), use the
operation catalog.

## The catalog

`amazon_ads.catalog` holds every operation from Amazon's 220 published OpenAPI specs:
method, path, parameters, and the versioned media types Amazon expects in
`Content-Type` and `Accept`. 1,094 of them, from 202 specs, are Ads API operations that
`call` sends. The other 110 are the tools of Amazon's hosted MCP server (see
[below](#operations-the-client-cannot-send)).

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

### Path parameters

Each path parameter fills exactly one segment of the operation's path, and Amazon reads
back exactly the value you pass. Pass plain values, never percent-encoded ones:

- Letters, digits and `-._~:@!$&'()*+,=` are sent as they are, so ids such as
  `amzn1.ads-account.g.EXAMPLE` arrive unchanged.
- Everything else is percent-encoded, including `%`, `;`, spaces and non-ASCII letters:
  `"100% Pure"` is sent as `100%25%20Pure`.
- A value that is already percent-encoded (it contains an escape such as `%20` or `%2F`)
  raises `ValueError`. Sending it encoded a second time would make Amazon read
  `My%20Brand` as those ten characters, not as `My Brand`. A literal `%` followed by two
  hex digits therefore cannot be sent.
- A value that could send the request to a different endpoint than the operation's
  raises `ValueError` naming the parameter, before anything is sent: one that contains
  `/`, `\`, `?`, `#` or a control character, or that is empty or only dots and spaces
  (also counting only the part before a `;`). `{"brandEntityId": "../sp/targets?x="}`
  would otherwise turn the read-only `stores:getInsightsForStoreAPI` into
  `POST /sp/targets`.
- `None`, `True`/`False` and floats raise `ValueError` too, rather than being sent as
  `None`, `True` or `1e+17`. Pass ids as strings or integers.

Every `{placeholder}` in the path must be filled, including the few that Amazon's specs
leave out of the operation's parameter list.

## Where the live API disagrees with the spec

Some operations refuse the media type their own spec documents. `catalog.CORRECTIONS`
records the type the live API accepts, and the catalog applies it when it loads, so
regenerating the catalog from Amazon's specs never undoes it. A corrected operation's
`op.accept` is what `call` sends and can be missing from `op.accepts`; `op.note` says
why. The [coverage](../reference/coverage.md) page marks these operations **corrected**,
and [API quirks](quirks.md) describes each one.

```python
op = catalog.get("advertising-billing:getAdvertiserInvoices")
op.accepts   # ('application/vnd.invoices.v1.1+json',), which Amazon answers with 406
op.accept    # 'application/json', what call() sends
op.note      # why

us.call("advertising-billing:getAdvertiserInvoices", query={"count": 10})["payload"]
```

## Operations the client cannot send

Amazon's documentation also publishes the tools of its hosted Ads MCP server (beta) as
OpenAPI files, and the catalog lists them as the `mcp-*` APIs, for example
`mcp-billing:list_invoices` at `POST /mcp/list_invoices`. They are not Ads API
endpoints: the Ads API hosts have no route for those paths and answer them with a 403.
Each one's `op.unsupported_reason` says so, `catalog.search` leaves them out unless you
pass `include_unsupported=True` or name their `api`, and `call` raises
`UnsupportedOperationError` before sending anything. Where a matching Ads API operation
is known, the error names it.

```python
catalog.search("invoices")                     # Ads API operations only
catalog.search("invoices", api="mcp-billing")  # the MCP tools, each with its reason
us.call("mcp-billing:list_invoices")           # UnsupportedOperationError: ... Use
                                               # advertising-billing:getAdvertiserInvoices instead.
```

## Read-only operations

`op.read_only` decides three things: whether `call` retries the operation after a 5xx
or a timeout, whether `adsctl ops call` asks before sending it, and whether the MCP
server's `call_operation` will run it at all. A write that timed out may still have
gone through, so only read-only operations are retried.

It is true for every GET, and for a POST when either of these holds and no operation id
or path segment starts with `create`, `update`, `delete` or `archive`:

- the operation id, or a whole path segment, starts with `list`, `query`, `get`,
  `search`, `describe`, `history` or `recommendation` (`listCampaigns`,
  `/adsApi/v1/query/targets`, `/sp/targets/bid/recommendations`);
- the path ends in `/list`, `/query`, `/history`, `/recommendations`,
  `/targets/categories`, `/targets/products/count` or `/budgetUsage`.

A word in the middle of a segment does not count, so `POST /sp/targets` is a write even
though "targets" contains "get". PUT, PATCH and DELETE are never read-only.

The rule reads names, not behavior, so it can be wrong both ways. A few reads with
unusual names count as writes, for example `dsp-quick-actions:batchGetExecutionsV1` and
`measurement:CheckDSPAudienceResearchEligibility`; send those with `call` directly or
with `adsctl ops call --yes`. In the other direction, `creative-assets:getUploadLocation`
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

`ops call` asks for confirmation before any operation that can change data, and
refuses the `mcp-*` tools without asking. `ops search --include-unsupported` lists those
tools too.

## Keeping the catalog current

The catalog is generated from Amazon's specs by `scripts/fetch_specs.py` and
`scripts/generate_catalog.py`. A scheduled workflow re-fetches them weekly and commits
the regenerated catalog to main when Amazon changes anything and the tests still pass.
Corrections live in `amazon_ads/catalog.py`, not in the generated file, so a refresh
keeps them. See [development](../development.md).
