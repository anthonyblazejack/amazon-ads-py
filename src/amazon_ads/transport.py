"""HTTP transport: headers, retries, throttling and error mapping.

Retry rules:

* **429 Too Many Requests** is always retried. Amazon rejected the request without acting
  on it, so resending cannot duplicate a write. The wait is Amazon's ``Retry-After`` header
  when present, otherwise exponential backoff with jitter. A 429 also pauses every other
  request made through the same transport until the wait is over, so parallel callers back
  off together instead of hammering a throttled account.
* **5xx and network timeouts** are retried only for requests that are safe to repeat:
  GETs and read-only POSTs such as ``/list`` and report polling. A create that times out
  may have succeeded, and resending it could create a duplicate campaign, so it raises.
* **Connection failures** (nothing was sent) are always retried.
* **401** invalidates the cached access token and retries once.
"""

from __future__ import annotations

import email.utils
import logging
import random
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Any

import httpx

from amazon_ads._version import __version__
from amazon_ads.auth import TokenProvider
from amazon_ads.errors import ApiError, ThrottledError, error_class_for
from amazon_ads.regions import SANDBOX_HOST, Region

log = logging.getLogger("amazon_ads")

RETRYABLE_SERVER_STATUSES = frozenset({500, 502, 503, 504})

# POST operations that only read. Amazon uses POST for most list and query calls.
READ_ONLY_POST_SUFFIXES = (
    "/list",
    "/history",
    "/recommendations",
    "/bid/recommendations",
    "/keywords/recommendations",
    "/targets/categories",
    "/targets/products/count",
    "/budgetUsage",
)


@dataclass(frozen=True)
class RetryPolicy:
    max_attempts: int = 8
    backoff_base: float = 1.0
    backoff_max: float = 60.0
    max_total_wait: float = 600.0

    def backoff(self, attempt: int) -> float:
        """Full-jitter exponential backoff for the given 1-based attempt number."""
        ceiling = min(self.backoff_max, self.backoff_base * (2 ** (attempt - 1)))
        return random.uniform(0, ceiling)


def parse_retry_after(value: str | None, *, now: float | None = None) -> float | None:
    """Seconds to wait from a ``Retry-After`` header (delta-seconds or an HTTP date)."""
    if not value:
        return None
    value = value.strip()
    try:
        return max(float(value), 0.0)
    except ValueError:
        pass
    try:
        parsed = email.utils.parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    current = time.time() if now is None else now
    return max(parsed.timestamp() - current, 0.0)


def is_idempotent(method: str, path: str) -> bool:
    method = method.upper()
    if method in {"GET", "HEAD", "OPTIONS"}:
        return True
    if method == "POST":
        return path.rstrip("/").endswith(READ_ONLY_POST_SUFFIXES)
    return False


class Transport:
    def __init__(
        self,
        tokens: TokenProvider,
        client_id: str,
        *,
        http: httpx.Client | None = None,
        retry: RetryPolicy | None = None,
        sandbox: bool = False,
        timeout: float = 60.0,
        user_agent: str | None = None,
        sleep: Callable[[float], None] = time.sleep,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self.tokens = tokens
        self.client_id = client_id
        self.retry = retry or RetryPolicy()
        self.sandbox = sandbox
        self._http = http or httpx.Client(timeout=timeout)
        self._owns_http = http is None
        self._user_agent = user_agent or f"amazon-ads-py/{__version__}"
        self._sleep = sleep
        self._monotonic = monotonic
        self._cooldown_lock = threading.Lock()
        self._cooldown_until = 0.0

    def close(self) -> None:
        if self._owns_http:
            self._http.close()

    def base_url(self, region: Region) -> str:
        return f"https://{SANDBOX_HOST}" if self.sandbox else region.base_url

    def request(
        self,
        method: str,
        path: str,
        *,
        region: Region,
        profile_id: str | int | None = None,
        json: Any = None,
        params: Mapping[str, Any] | None = None,
        content_type: str | None = None,
        accept: str | None = None,
        headers: Mapping[str, str] | None = None,
        idempotent: bool | None = None,
        absolute_url: str | None = None,
    ) -> httpx.Response:
        """Send one API call, retrying as described in the module docstring.

        Returns the response for any 2xx status, including 207 Multi-Status, which the
        caller must inspect item by item. Raises an :class:`ApiError` subclass otherwise.
        """
        url = absolute_url or self.base_url(region) + path
        safe = is_idempotent(method, path) if idempotent is None else idempotent
        attempt = 0
        waited = 0.0
        refreshed_token = False

        while True:
            attempt += 1
            self._wait_for_cooldown()
            request_headers = self._headers(profile_id, content_type, accept, json, headers)
            try:
                response = self._http.request(
                    method,
                    url,
                    json=json,
                    params=_clean_params(params),
                    headers=request_headers,
                )
            except httpx.TransportError as exc:
                nothing_sent = isinstance(exc, httpx.ConnectError | httpx.ConnectTimeout)
                if (nothing_sent or safe) and attempt < self.retry.max_attempts:
                    delay = self.retry.backoff(attempt)
                    if waited + delay <= self.retry.max_total_wait:
                        log.warning("%s %s failed (%s); retrying in %.1fs", method, url, exc, delay)
                        self._sleep(delay)
                        waited += delay
                        continue
                raise

            status = response.status_code
            log.debug(
                "%s %s -> %s [%s]", method, url, status, response.headers.get("x-amz-request-id")
            )
            if status < 400:
                return response

            if status == 401 and not refreshed_token:
                refreshed_token = True
                self.tokens.invalidate()
                continue

            retryable = status == 429 or (status in RETRYABLE_SERVER_STATUSES and safe)
            if retryable and attempt < self.retry.max_attempts:
                retry_after = parse_retry_after(response.headers.get("Retry-After"))
                delay = retry_after if retry_after is not None else self.retry.backoff(attempt)
                if waited + delay <= self.retry.max_total_wait:
                    if status == 429:
                        self._start_cooldown(delay)
                        log.info("Throttled on %s %s; waiting %.1fs", method, url, delay)
                    else:
                        log.warning("%s on %s %s; retrying in %.1fs", status, method, url, delay)
                        self._sleep(delay)
                    waited += delay
                    continue

            raise build_error(response)

    def download(self, url: str) -> bytes:
        """Fetch a pre-signed URL (report files). No Ads API headers are sent."""
        attempt = 0
        while True:
            attempt += 1
            try:
                response = self._http.get(url, follow_redirects=True)
            except httpx.TransportError:
                if attempt >= self.retry.max_attempts:
                    raise
                self._sleep(self.retry.backoff(attempt))
                continue
            retryable = response.status_code in RETRYABLE_SERVER_STATUSES | {429}
            if retryable and attempt < self.retry.max_attempts:
                self._sleep(self.retry.backoff(attempt))
                continue
            if response.status_code >= 400:
                raise build_error(response)
            return response.content

    def _headers(
        self,
        profile_id: str | int | None,
        content_type: str | None,
        accept: str | None,
        body: Any,
        extra: Mapping[str, str] | None,
    ) -> dict[str, str]:
        headers = {
            "Authorization": f"Bearer {self.tokens.token()}",
            "Amazon-Advertising-API-ClientId": self.client_id,
            "User-Agent": self._user_agent,
            "Accept": accept or content_type or "application/json",
        }
        if profile_id is not None:
            headers["Amazon-Advertising-API-Scope"] = str(profile_id)
        if body is not None:
            headers["Content-Type"] = content_type or "application/json"
        if extra:
            headers.update(extra)
        return headers

    def _start_cooldown(self, seconds: float) -> None:
        with self._cooldown_lock:
            self._cooldown_until = max(self._cooldown_until, self._monotonic() + seconds)

    def _wait_for_cooldown(self) -> None:
        with self._cooldown_lock:
            remaining = self._cooldown_until - self._monotonic()
        if remaining > 0:
            self._sleep(remaining)


def _clean_params(params: Mapping[str, Any] | None) -> dict[str, Any] | None:
    if params is None:
        return None
    cleaned: dict[str, Any] = {}
    for key, value in params.items():
        if value is None:
            continue
        if isinstance(value, list | tuple | set):
            cleaned[key] = ",".join(str(v) for v in value)
        elif isinstance(value, bool):
            cleaned[key] = str(value).lower()
        else:
            cleaned[key] = value
    return cleaned


def build_error(response: httpx.Response) -> ApiError:
    """Turn an error response into the matching :class:`ApiError` subclass.

    Amazon's error bodies differ by API: ``{"code", "details"}``, ``{"code", "message"}``,
    ``{"message"}`` or ``{"errors": [...]}``. All of them end up in ``message`` and ``code``.
    """
    try:
        body: Any = response.json()
    except ValueError:
        body = response.text
    code: str | None = None
    message = response.reason_phrase or "Request failed"
    if isinstance(body, dict):
        code = body.get("code") or body.get("errorCode") or body.get("error")
        message = str(
            body.get("details")
            or body.get("detail")
            or body.get("message")
            or body.get("description")
            or _first_error_message(body.get("errors"))
            or message
        )
    elif isinstance(body, str) and body.strip():
        message = body.strip()[:500]
    cls = error_class_for(response.status_code)
    kwargs: dict[str, Any] = {
        "status_code": response.status_code,
        "code": str(code) if code is not None else None,
        "request_id": response.headers.get("x-amz-request-id")
        or response.headers.get("x-amzn-RequestId"),
        "body": body,
        "method": response.request.method if response.request else None,
        "url": str(response.request.url) if response.request else None,
    }
    if cls is ThrottledError:
        return ThrottledError(
            message, retry_after=parse_retry_after(response.headers.get("Retry-After")), **kwargs
        )
    return cls(message, **kwargs)


def _first_error_message(errors: Any) -> str | None:
    if isinstance(errors, list) and errors:
        first = errors[0]
        if isinstance(first, dict):
            return str(first.get("message") or first.get("details") or first)
        return str(first)
    return None
