"""Generic create, list, update and delete for Sponsored Products v3 entities.

Every SP v3 entity follows the same contract:

* ``POST {path}/list`` with filters and a ``nextToken`` for paging,
* ``POST {path}`` to create, ``PUT {path}`` to update, ``POST {path}/delete`` to archive,
* a versioned media type (``application/vnd.spKeyword.v3+json``) on both
  ``Content-Type`` and ``Accept``,
* 207 Multi-Status answers to every write.

:class:`SpResource` implements that once. Each entity is an :class:`EntitySpec`.
"""

from __future__ import annotations

import builtins
import dataclasses
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Generic, TypeVar

from amazon_ads.batch import BatchResult, parse_multi_status
from amazon_ads.models import ApiModel
from amazon_ads.plans import ChangePlan, PlannedChange

if TYPE_CHECKING:
    from amazon_ads.client import ProfileClient

M = TypeVar("M", bound=ApiModel)


class _Unset:
    """Marks an argument the caller did not pass, where ``None`` has its own meaning."""


UNSET = _Unset()

EntityInput = Mapping[str, Any] | ApiModel


@dataclass(frozen=True)
class EntitySpec(Generic[M]):
    kind: str  # stable name used in change plans, e.g. "sp.keywords"
    path: str  # "/sp/keywords"
    media_type: str  # "application/vnd.spKeyword.v3+json"
    collection_key: str  # "keywords"
    id_field: str  # "keywordId": the id's name on the entity and in update payloads
    id_filter: str  # "keywordIdFilter": the list and delete filter for that id
    model: type[M]
    updatable: frozenset[str]
    # The id's name in 207 success entries. Negatives differ from their entity field:
    # a negative keyword is updated by "keywordId" but reported as "negativeKeywordId".
    result_id_field: str | None = None
    max_batch: int = 1000
    max_filter_ids: int = 1000
    # States listed when the caller does not say. None lists every state.
    default_states: tuple[str, ...] | None = ("ENABLED", "PAUSED")

    @property
    def success_id_field(self) -> str:
        return self.result_id_field or self.id_field


class SpResource(Generic[M]):
    def __init__(self, client: ProfileClient, spec: EntitySpec[M]) -> None:
        self._client = client
        self.spec = spec

    # --- reads --------------------------------------------------------------------------

    def iter(
        self,
        *,
        ids: Iterable[str | int] | None = None,
        campaign_ids: Iterable[str | int] | None = None,
        ad_group_ids: Iterable[str | int] | None = None,
        states: Iterable[str] | _Unset | None = UNSET,
        name: str | None = None,
        extended: bool = False,
        page_size: int | None = None,
        filters: Mapping[str, Any] | None = None,
    ) -> Iterator[M]:
        """Yield every matching entity, fetching further pages as needed.

        ``states`` defaults to enabled and paused (every state for portfolios); pass
        ``None`` for every state including archived. ``name`` is an exact-name match for
        campaigns and ad groups. ``filters`` is merged into the request body for anything
        not covered here.
        """
        if isinstance(states, _Unset):
            states = self.spec.default_states
        states = [s.upper() for s in states] if states is not None else None
        campaign_ids = list(campaign_ids) if campaign_ids is not None else None
        ad_group_ids = list(ad_group_ids) if ad_group_ids is not None else None
        # Amazon defaults maxResults to each API's maximum page size, so it is only sent
        # when the caller asks for smaller pages.
        body: dict[str, Any] = {"maxResults": page_size} if page_size else {}
        if ids is not None:
            body[self.spec.id_filter] = {"include": [str(i) for i in ids]}
        if campaign_ids is not None:
            body["campaignIdFilter"] = {"include": [str(i) for i in campaign_ids]}
        if ad_group_ids is not None:
            body["adGroupIdFilter"] = {"include": [str(i) for i in ad_group_ids]}
        if states is not None:
            body["stateFilter"] = {"include": states}
        if name is not None:
            body["nameFilter"] = {"queryTermMatchType": "EXACT_MATCH", "include": [name]}
        if extended:
            body["includeExtendedDataFields"] = True
        if filters:
            body.update(filters)

        # Amazon caps an id filter at 1000 ids, so longer lists are sent in slices.
        id_values = body.get(self.spec.id_filter, {}).get("include")
        if id_values and len(id_values) > self.spec.max_filter_ids:
            for chunk in _chunks(id_values, self.spec.max_filter_ids):
                yield from self.iter(
                    ids=chunk,
                    campaign_ids=campaign_ids,
                    ad_group_ids=ad_group_ids,
                    states=states,
                    name=name,
                    extended=extended,
                    page_size=page_size,
                    filters=filters,
                )
            return

        while True:
            response = self._client.request(
                "POST",
                f"{self.spec.path}/list",
                json=body,
                content_type=self.spec.media_type,
            )
            payload = response.json()
            for raw in payload.get(self.spec.collection_key) or []:
                yield self.spec.model.model_validate(raw)
            token = payload.get("nextToken")
            if not token:
                return
            body = {**body, "nextToken": token}

    def list(self, **kwargs: Any) -> list[M]:
        """Every matching entity as a list. Takes the same arguments as :meth:`iter`."""
        return list(self.iter(**kwargs))

    def get(self, entity_id: str | int) -> M | None:
        """One entity by id, in any state, or ``None`` if Amazon has no such entity."""
        found = self.list(ids=[entity_id], states=None)
        return found[0] if found else None

    def get_many(self, ids: Iterable[str | int]) -> dict[str, M]:
        """Entities by id, in any state, keyed by id. Missing ids are simply absent."""
        result: dict[str, M] = {}
        for entity in self.iter(ids=ids, states=None):
            entity_id = _entity_id(entity, self.spec)
            if entity_id is not None:
                result[entity_id] = entity
        return result

    # --- writes -------------------------------------------------------------------------

    def create(self, items: Sequence[EntityInput]) -> BatchResult[dict[str, Any]]:
        """Create entities. Returns per-item results; never raises on item failures.

        Call :meth:`BatchResult.raise_for_errors` to turn any rejected item into an
        exception.
        """
        payloads = [_to_payload(item) for item in items]
        return self._write("POST", self.spec.path, payloads)

    def update(self, items: Sequence[EntityInput]) -> BatchResult[dict[str, Any]]:
        """Update entities. Each item needs the id field plus the fields to change.

        Fields Amazon does not allow changing (for example a keyword's text or match
        type) are dropped from the payload rather than sent and rejected.
        """
        payloads = [self._update_payload(item) for item in items]
        return self._write("PUT", self.spec.path, payloads)

    def delete(self, ids: Sequence[str | int]) -> BatchResult[dict[str, Any]]:
        """Archive entities. Amazon has no hard delete; archived entities stay listable
        with ``states=["ARCHIVED"]`` and cannot be re-enabled."""
        items = [{self.spec.id_field: str(i)} for i in ids]
        id_field = self.spec.id_field
        return self._send(
            items,
            lambda chunk: self._client.request(
                "POST",
                f"{self.spec.path}/delete",
                json={self.spec.id_filter: {"include": [c[id_field] for c in chunk]}},
                content_type=self.spec.media_type,
            ).json(),
            repeatable=True,
        )

    def set_state(self, ids: Sequence[str | int], state: str) -> BatchResult[dict[str, Any]]:
        """Enable or pause entities. Use :meth:`delete` to archive."""
        return self.update([{self.spec.id_field: str(i), "state": state.upper()} for i in ids])

    def pause(self, ids: Sequence[str | int]) -> BatchResult[dict[str, Any]]:
        return self.set_state(ids, "PAUSED")

    def enable(self, ids: Sequence[str | int]) -> BatchResult[dict[str, Any]]:
        return self.set_state(ids, "ENABLED")

    # --- change plans -------------------------------------------------------------------

    def plan_update(self, items: Sequence[EntityInput], *, note: str | None = None) -> ChangePlan:
        """Build a reviewable :class:`ChangePlan` for an update without sending it.

        Reads each entity's current values so the plan shows before and after for every
        field and can be reversed. Items whose values already match are left out. Ids
        Amazon does not return are reported in ``plan.missing``.
        """
        payloads = [self._update_payload(item) for item in items]
        ids = [str(p[self.spec.id_field]) for p in payloads]
        current = self.get_many(ids)
        changes: list[PlannedChange] = []
        missing: list[str] = []
        for payload in payloads:
            entity_id = str(payload[self.spec.id_field])
            entity = current.get(entity_id)
            if entity is None:
                missing.append(entity_id)
                continue
            before_all = entity.to_api()
            before: dict[str, Any] = {}
            after: dict[str, Any] = {}
            for field, value in payload.items():
                if field == self.spec.id_field:
                    continue
                old = before_all.get(field)
                if old != value:
                    before[field] = old
                    after[field] = value
            if after:
                changes.append(
                    PlannedChange(
                        kind=self.spec.kind,
                        id=entity_id,
                        before=before,
                        after=after,
                        label=_label(entity),
                    )
                )
        return ChangePlan.new(
            profile_id=self._client.profile_id,
            country_code=self._client.country_code,
            changes=changes,
            missing=missing,
            note=note,
        )

    def plan_create(self, items: Sequence[EntityInput], *, note: str | None = None) -> ChangePlan:
        """A plan that creates entities. Its rollback, built after applying, archives the
        ids Amazon assigned."""
        changes = []
        for item in items:
            payload = _to_payload(item)
            payload.pop(self.spec.id_field, None)
            changes.append(
                PlannedChange(
                    kind=self.spec.kind,
                    id=None,
                    before={},
                    after=payload,
                    label=_label_from(payload),
                    op="create",
                )
            )
        return ChangePlan.new(
            profile_id=self._client.profile_id,
            country_code=self._client.country_code,
            changes=changes,
            note=note,
        )

    def plan_archive(self, ids: Sequence[str | int], *, note: str | None = None) -> ChangePlan:
        """A plan that archives entities. Amazon cannot re-enable an archived entity, so
        this plan has no rollback; pausing (:meth:`plan_update` with ``state``) does."""
        current = self.get_many(ids)
        changes = []
        missing = []
        for entity_id in (str(i) for i in ids):
            entity = current.get(entity_id)
            if entity is None:
                missing.append(entity_id)
                continue
            changes.append(
                PlannedChange(
                    kind=self.spec.kind,
                    id=entity_id,
                    before={"state": entity.to_api().get("state")},
                    after={"state": "ARCHIVED"},
                    label=_label(entity),
                    op="archive",
                )
            )
        return ChangePlan.new(
            profile_id=self._client.profile_id,
            country_code=self._client.country_code,
            changes=changes,
            missing=missing,
            note=note,
            warnings=["Archiving cannot be undone."] if changes else [],
        )

    # --- internals ----------------------------------------------------------------------

    def _update_payload(self, item: EntityInput) -> dict[str, Any]:
        payload = _to_payload(item)
        if self.spec.id_field not in payload:
            raise ValueError(f"Update items need {self.spec.id_field}: {payload}")
        return {k: v for k, v in payload.items() if k in self.spec.updatable}

    def _write(
        self, method: str, path: str, payloads: builtins.list[dict[str, Any]]
    ) -> BatchResult[dict[str, Any]]:
        return self._send(
            payloads,
            lambda chunk: self._client.request(
                method,
                path,
                json={self.spec.collection_key: chunk},
                content_type=self.spec.media_type,
            ).json(),
            # Re-sending an update or archive sets the same values again. Re-sending a
            # create after an internal error could make a second copy, so creates are only
            # retried when Amazon said it throttled them.
            repeatable=method != "POST",
        )

    def _send(
        self,
        items: builtins.list[dict[str, Any]],
        send_chunk: Callable[[builtins.list[dict[str, Any]]], Any],
        *,
        repeatable: bool,
    ) -> BatchResult[dict[str, Any]]:
        """Send items in chunks of the batch limit and parse every 207 answer.

        Items Amazon rejected as throttled (and, for repeatable writes, internal errors)
        are sent again with backoff, alone, so one busy moment does not fail part of a
        batch. Every result keeps the index of its item in ``items``.
        """
        transport = self._client.account.transport
        result: BatchResult[dict[str, Any]] = BatchResult()
        pending = list(enumerate(items))
        attempt = 0
        while pending:
            attempt += 1
            retry: builtins.list[tuple[int, dict[str, Any]]] = []
            for chunk in _chunks(pending, self.spec.max_batch):
                sent = [item for _, item in chunk]
                parsed = parse_multi_status(
                    send_chunk(sent),
                    sent,
                    entity_key=self.spec.collection_key,
                    id_field=self.spec.success_id_field,
                )
                for success in parsed.successes:
                    original = chunk[success.index][0] if success.index < len(chunk) else -1
                    result.successes.append(dataclasses.replace(success, index=original))
                for error in parsed.errors:
                    original = chunk[error.index][0] if error.index < len(chunk) else -1
                    throttled = error.error_type == "throttledError" or error.code == "THROTTLED"
                    again = error.transient and (repeatable or throttled)
                    if (
                        again
                        and attempt < transport.retry.max_attempts
                        and error.index < len(chunk)
                    ):
                        retry.append(chunk[error.index])
                    else:
                        result.errors.append(dataclasses.replace(error, index=original))
            pending = retry
            if pending:
                self._client.account.sleep(transport.retry.backoff(attempt))
        result.successes.sort(key=lambda s: s.index)
        result.errors.sort(key=lambda e: e.index)
        return result


def _to_payload(item: EntityInput) -> dict[str, Any]:
    if isinstance(item, ApiModel):
        return item.to_api()
    # Accept snake_case keys in plain dicts too, so callers can mix styles.
    from pydantic.alias_generators import to_camel

    return {(k if "_" not in k else to_camel(k)): v for k, v in dict(item).items()}


def _entity_id(entity: ApiModel, spec: EntitySpec[Any]) -> str | None:
    value = entity.to_api().get(spec.id_field)
    return str(value) if value is not None else None


def _label(entity: ApiModel) -> str | None:
    return _label_from(entity.to_api())


def _label_from(data: dict[str, Any]) -> str | None:
    for key in ("keywordText", "name", "asin"):
        if data.get(key):
            match = data.get("matchType")
            return f"{data[key]} [{match}]" if match else str(data[key])
    expression = data.get("expression")
    if isinstance(expression, list) and expression:
        return ", ".join(f"{e.get('type')}={e.get('value')}" for e in expression)
    return None


def _chunks(items: Sequence[Any], size: int) -> Iterator[list[Any]]:
    for start in range(0, len(items), size):
        yield list(items[start : start + size])
