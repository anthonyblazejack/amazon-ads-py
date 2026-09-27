# Errors and throttling

## Exceptions

Everything the library raises derives from `AmazonAdsError`.

| Exception | When |
|---|---|
| `ConfigurationError` | Missing credentials, unknown or ambiguous market |
| `AuthError` | Login with Amazon refused a token (`.error`, e.g. `invalid_grant`) |
| `BadRequestError` | 400 |
| `UnauthorizedError` | 401 after a token refresh |
| `ForbiddenError` | 403: no access to this profile or operation |
| `NotFoundError` | 404 |
| `UnprocessableError` | 422: valid request Amazon will not act on |
| `ThrottledError` | 429 after every retry (`.retry_after`) |
| `ServerError` | 5xx |
| `PartialFailureError` | Some batch items failed, from `raise_for_errors()` (`.result`) |
| `ReportFailedError` | A report failed or timed out (`.report_id`, `.status`) |
| `PlanDriftError` | A plan's before values no longer match the account (`.drifted`) |

Every `ApiError` carries `status_code`, Amazon's `code`, the message, the parsed `body`,
the `method` and `url`, and `request_id` (Amazon support asks for this).

```python
from amazon_ads import ApiError

try:
    us.sp.keywords.list()
except ApiError as exc:
    print(exc.status_code, exc.code, exc, exc.request_id)
```

## Retries

| Response | Retried? | Wait |
|---|---|---|
| 429 Too Many Requests | Always: Amazon rejected it unprocessed | `Retry-After` if sent, else exponential backoff with jitter |
| 500, 502, 503, 504 | Only requests safe to repeat (reads, lists, report polling) | Exponential backoff with jitter |
| Timeout after sending | Only requests safe to repeat | Exponential backoff with jitter |
| Connection refused (nothing sent) | Always | Exponential backoff with jitter |
| 401 | Once, after refreshing the token | None |

A write that fails with 5xx or times out may still have been applied, and resending a
create could duplicate campaigns or keywords. So writes raise instead of retrying: check
the account (list the entities) before trying again.

The same rules apply to single items inside a 207 batch response. An item Amazon marks
`throttledError` is sent again on its own with backoff. An item marked
`internalServerError` is sent again for updates and archives, but not for creates. Every
other per-item error comes back in the `BatchResult` with its `hint` and any allowed range
(see [batch results](campaign-management.md#batch-results)).

A 429 also starts a **shared cooldown**. Every other request through the same client
waits it out too, instead of making things worse.

Tune it:

```python
from amazon_ads import AmazonAds, RetryPolicy

ads = AmazonAds.from_env(retry=RetryPolicy(max_attempts=10, backoff_max=120, max_total_wait=900))
```

## Logging

The library logs to the `amazon_ads` logger: throttling waits at INFO, retries at
WARNING, every request with its status and Amazon request id at DEBUG.

```python
import logging
logging.basicConfig()
logging.getLogger("amazon_ads").setLevel(logging.DEBUG)
```
