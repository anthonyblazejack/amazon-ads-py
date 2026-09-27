"""Exceptions raised by the client.

Every HTTP failure becomes an :class:`ApiError` subclass carrying the status code, Amazon's
error code and message, and the ``x-amz-request-id`` Amazon support asks for when a call
misbehaves.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from amazon_ads.batch import BatchResult


class AmazonAdsError(Exception):
    """Base class for everything this library raises."""


class ConfigurationError(AmazonAdsError):
    """Credentials or settings are missing or inconsistent."""


class AuthError(AmazonAdsError):
    """Login with Amazon refused to issue an access token.

    ``invalid_grant`` usually means the refresh token expired (365 days after consent for
    tokens issued since mid 2026) or the client secret was reset, which revokes every
    refresh token issued under it.
    """

    def __init__(self, message: str, *, error: str | None = None) -> None:
        super().__init__(message)
        self.error = error


class ApiError(AmazonAdsError):
    """The Ads API answered with an error status."""

    def __init__(
        self,
        message: str,
        *,
        status_code: int,
        code: str | None = None,
        request_id: str | None = None,
        body: Any = None,
        method: str | None = None,
        url: str | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.request_id = request_id
        self.body = body
        self.method = method
        self.url = url

    def __str__(self) -> str:
        parts = [f"{self.status_code}"]
        if self.code:
            parts.append(self.code)
        parts.append(super().__str__())
        if self.method and self.url:
            parts.append(f"({self.method} {self.url})")
        if self.request_id:
            parts.append(f"[request id {self.request_id}]")
        return " ".join(parts)


class BadRequestError(ApiError):
    """400: the request was malformed."""


class UnauthorizedError(ApiError):
    """401: the access token was rejected even after a refresh."""


class ForbiddenError(ApiError):
    """403: the token is valid but lacks access to this profile or operation."""


class NotFoundError(ApiError):
    """404: the entity, report or path does not exist."""


class UnprocessableError(ApiError):
    """422: the request was well formed but Amazon will not act on it.

    Amazon also uses 422 for unsupported combinations, for example a bid recommendation
    request that mixes keyword and product (ASIN) targeting expressions.
    """


class ThrottledError(ApiError):
    """429: still throttled after every retry was spent."""

    def __init__(self, *args: Any, retry_after: float | None = None, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.retry_after = retry_after


class ServerError(ApiError):
    """5xx from Amazon."""


class PartialFailureError(AmazonAdsError):
    """A batch write where Amazon rejected some or all items.

    Amazon answers batch writes with 207 Multi-Status: items succeed or fail one by one,
    and the call as a whole still returns 207. ``result`` holds both lists, so the items
    that did go through are never lost.
    """

    def __init__(self, result: BatchResult[Any]) -> None:
        self.result = result
        failed = len(result.errors)
        total = failed + len(result.successes)
        first = result.errors[0].message if result.errors else ""
        super().__init__(f"{failed} of {total} items failed. First error: {first}")


class ReportFailedError(AmazonAdsError):
    """A report finished with status FAILURE, or never finished before the timeout."""

    def __init__(self, message: str, *, report_id: str, status: str | None = None) -> None:
        super().__init__(message)
        self.report_id = report_id
        self.status = status


class PlanDriftError(AmazonAdsError):
    """The account no longer holds the values a change plan was built against.

    Raised by :meth:`ChangePlan.apply` with ``check_drift=True`` so a stale plan cannot
    overwrite a change someone made after the plan was reviewed.
    """

    def __init__(self, message: str, *, drifted: list[dict[str, Any]]) -> None:
        super().__init__(message)
        self.drifted = drifted


def error_class_for(status_code: int) -> type[ApiError]:
    if status_code == 400:
        return BadRequestError
    if status_code == 401:
        return UnauthorizedError
    if status_code == 403:
        return ForbiddenError
    if status_code == 404:
        return NotFoundError
    if status_code == 422:
        return UnprocessableError
    if status_code == 429:
        return ThrottledError
    if status_code >= 500:
        return ServerError
    return ApiError
