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


@dataclass(frozen=True)
class BatchError(Generic[T]):
    index: int
    item: T | None
    code: str | None
    message: str
    raw: Any = None


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
        code, message = _describe_errors(entry.get("errors") or [])
        result.errors.append(BatchError(index, item_at(index), code, message, entry))

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


def _describe_errors(errors: list[Any]) -> tuple[str | None, str]:
    """Pull a readable code and message out of SP v3's nested ``errorValue`` objects."""
    codes: list[str] = []
    messages: list[str] = []
    for err in errors:
        if not isinstance(err, dict):
            messages.append(str(err))
            continue
        error_type = err.get("errorType")
        value = err.get("errorValue")
        detail: dict[str, Any] = {}
        if isinstance(value, dict):
            inner = value.get(error_type) if error_type else None
            if isinstance(inner, dict):
                detail = inner
            else:
                detail = next((v for v in value.values() if isinstance(v, dict)), value)
        code = detail.get("reason") or error_type or err.get("code")
        if code:
            codes.append(str(code))
        message = detail.get("message") or err.get("message") or err.get("details")
        messages.append(str(message or code or err))
    return (codes[0] if codes else None), "; ".join(messages) or "Unknown error"
