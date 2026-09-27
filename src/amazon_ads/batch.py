"""Results of batch writes (Amazon's 207 Multi-Status responses).

Amazon accepts or rejects each item in a batch create, update or delete on its own, and
answers 207 even when every item failed. Two response shapes exist:

* Sponsored Products v3 and newer APIs::

    {"keywords": {"success": [{"index": 0, "keywordId": "1", "keyword": {...}}],
                  "error":   [{"index": 1, "errors": [{"errorType": "...",
                                                       "errorValue": {...}}]}]}}

* Older APIs (Sponsored Display, Sponsored Brands v3) return a list in request order::

    [{"code": "SUCCESS", "keywordId": 1}, {"code": "INVALID_ARGUMENT", "description": "..."}]

:func:`parse_multi_status` reads both into a :class:`BatchResult`, where every success and
error carries the index of the item in the caller's original list, even when the library
split that list into several requests.
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from dataclasses import dataclass, field
from typing import Any, Generic, TypeVar

from amazon_ads.errors import PartialFailureError

T = TypeVar("T")


@dataclass(frozen=True)
class BatchSuccess(Generic[T]):
    index: int
    id: str | None
    item: T | None
    entity: dict[str, Any] | None = None


# Per-item failures that mean Amazon did not act on the item, so sending it again is
# safe. Anything else (a bid out of range, an archived parent, a duplicate) fails the same
# way every time and is returned to the caller instead.
TRANSIENT_ERROR_TYPES = frozenset({"throttledError", "internalServerError"})
TRANSIENT_CODES = frozenset({"THROTTLED", "INTERNAL_ERROR", "SERVER_IS_BUSY"})


@dataclass(frozen=True)
class BatchError(Generic[T]):
    """One item Amazon rejected.

    ``error_type`` is Amazon's error family (``biddingError``, ``entityStateError``, ...),
    ``code`` its reason (``BID_OUT_OF_MARKET_PLACE_RANGE``, ``TOO_LOW``, ...). Bid and range
    errors carry the allowed ``lower_limit`` and ``upper_limit``. :attr:`hint` says what to
    do about it in plain words.
    """

    index: int
    item: T | None
    code: str | None
    message: str
    raw: Any = None
    error_type: str | None = None
    lower_limit: float | None = None
    upper_limit: float | None = None

    @property
    def transient(self) -> bool:
        """Amazon did not act on the item (throttled or an internal error)."""
        return self.error_type in TRANSIENT_ERROR_TYPES or (self.code or "") in TRANSIENT_CODES

    @property
    def hint(self) -> str:
        if self.lower_limit is not None or self.upper_limit is not None:
            return f"Allowed range is {self.lower_limit} to {self.upper_limit}."
        family = self.error_type or ""
        if family == "throttledError" or self.code == "THROTTLED":
            return "Amazon kept throttling this item after retries; send it again later."
        if family == "internalServerError" or self.code == "INTERNAL_ERROR":
            return (
                "Amazon failed internally on this item. For a create, check whether it exists"
                " before sending it again."
            )
        if family == "entityNotFoundError":
            return "No entity with this id in this profile."
        if family == "duplicateValueError":
            return "It already exists; nothing new was created."
        if family == "entityStateError":
            return "The entity or its parent is archived or paused in a way that forbids this."
        if family in {"missingValueError", "malformedValueError", "invalidInputError"}:
            return "Fix the field named in the message and send it again."
        if family == "entityQuotaError":
            return "An Amazon account limit was reached (too many of this entity)."
        return "See Amazon's message."


@dataclass
class BatchResult(Generic[T]):
    successes: list[BatchSuccess[T]] = field(default_factory=list)
    errors: list[BatchError[T]] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors

    @property
    def ids(self) -> list[str]:
        return [s.id for s in self.successes if s.id is not None]

    def raise_for_errors(self) -> BatchResult[T]:
        """Raise :class:`PartialFailureError` if any item failed; return self otherwise."""
        if self.errors:
            raise PartialFailureError(self)
        return self

    def extend(self, other: BatchResult[T]) -> None:
        self.successes.extend(other.successes)
        self.errors.extend(other.errors)
        self.successes.sort(key=lambda s: s.index)
        self.errors.sort(key=lambda e: e.index)

    def __iter__(self) -> Iterator[BatchSuccess[T] | BatchError[T]]:
        combined: list[BatchSuccess[T] | BatchError[T]] = [*self.successes, *self.errors]
        return iter(sorted(combined, key=lambda r: r.index))

    def summary(self) -> str:
        return f"{len(self.successes)} succeeded, {len(self.errors)} failed"


def parse_multi_status(
    body: Any,
    items: Sequence[T] | None = None,
    *,
    entity_key: str | None = None,
    id_field: str | None = None,
    offset: int = 0,
) -> BatchResult[T]:
    """Read a 207 body into a :class:`BatchResult`.

    ``items`` is the list that was sent, so each result can be paired with its input.
    ``offset`` is added to Amazon's indexes when this body answers one chunk of a larger
    list. ``entity_key`` names the wrapper key (``"keywords"``) and ``id_field`` the id
    (``"keywordId"``); both are inferred when omitted.
    """
    items = items or []

    def item_at(i: int) -> T | None:
        local = i - offset
        return items[local] if 0 <= local < len(items) else None

    result: BatchResult[T] = BatchResult()

    if isinstance(body, list):
        for local_index, entry in enumerate(body):
            index = offset + local_index
            if not isinstance(entry, dict):
                continue
            code = entry.get("code")
            if code == "SUCCESS" or (code is None and not entry.get("errors")):
                result.successes.append(
                    BatchSuccess(index, _find_id(entry, id_field), item_at(index), entry)
                )
            else:
                message = str(entry.get("description") or entry.get("details") or code)
                result.errors.append(
                    BatchError(index, item_at(index), str(code) if code else None, message, entry)
                )
        return result

    if not isinstance(body, dict):
        return result

    container = _find_container(body, entity_key)
    singular = _singular(entity_key) if entity_key else None

    for entry in container.get("success") or []:
        index = offset + int(entry.get("index", 0))
        entity = entry.get(singular) if singular else None
        if entity is None:
            entity = next(
                (v for k, v in entry.items() if isinstance(v, dict) and k != "index"), None
            )
        result.successes.append(
            BatchSuccess(index, _find_id(entry, id_field), item_at(index), entity)
        )

    for entry in container.get("error") or []:
        index = offset + int(entry.get("index", 0))
        info = _describe_errors(entry.get("errors") or [])
        result.errors.append(
            BatchError(
                index,
                item_at(index),
                info["code"],
                info["message"],
                entry,
                error_type=info["error_type"],
                lower_limit=info["lower"],
                upper_limit=info["upper"],
            )
        )

    return result


def _find_container(body: dict[str, Any], entity_key: str | None) -> dict[str, Any]:
    if entity_key and isinstance(body.get(entity_key), dict):
        return dict(body[entity_key])
    if "success" in body or "error" in body:
        return body
    for value in body.values():
        if isinstance(value, dict) and ("success" in value or "error" in value):
            return dict(value)
    return {}


def _singular(key: str) -> str:
    if key.endswith("ies"):
        return key[:-3] + "y"
    return key[:-1] if key.endswith("s") else key


def _find_id(entry: dict[str, Any], id_field: str | None) -> str | None:
    if id_field and entry.get(id_field) is not None:
        return str(entry[id_field])
    for key, value in entry.items():
        if key.endswith("Id") and isinstance(value, str | int):
            return str(value)
    return None


def _describe_errors(errors: list[Any]) -> dict[str, Any]:
    """Pull a readable code, message, error family and any allowed range out of SP v3's
    nested ``errorValue`` objects."""
    codes: list[str] = []
    messages: list[str] = []
    error_type: str | None = None
    lower: float | None = None
    upper: float | None = None
    for err in errors:
        if not isinstance(err, dict):
            messages.append(str(err))
            continue
        family = err.get("errorType")
        value = err.get("errorValue")
        detail: dict[str, Any] = {}
        if isinstance(value, dict):
            if not family and len(value) == 1:
                family = next(iter(value))
            inner = value.get(family) if family else None
            if isinstance(inner, dict):
                detail = inner
            else:
                detail = next((v for v in value.values() if isinstance(v, dict)), value)
        error_type = error_type or (str(family) if family else None)
        code = detail.get("reason") or family or err.get("code")
        if code:
            codes.append(str(code))
        lower = lower if lower is not None else _number(detail.get("lowerLimit"))
        upper = upper if upper is not None else _number(detail.get("upperLimit"))
        message = detail.get("message") or err.get("message") or err.get("details")
        messages.append(str(message or code or err))
    return {
        "code": codes[0] if codes else None,
        "message": "; ".join(messages) or "Unknown error",
        "error_type": error_type,
        "lower": lower,
        "upper": upper,
    }


def _number(value: Any) -> float | None:
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None
