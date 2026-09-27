"""Change history (``POST /history``): every change to a Sponsored Products or Sponsored
Brands entity over the last 90 days, with its previous and new value and a timestamp.

Amazon does not record who made a change. Sponsored Display changes are not included.

Beyond listing events this module answers two practical questions:

* :meth:`History.value_at`: what was this keyword's bid (or state) at a given moment?
  Report rows carry the bid at pull time, not on the row's date, so this is the way to
  know what a bid actually was on a past day.
* :meth:`History.rollback_plan`: a :class:`~amazon_ads.plans.ChangePlan` that puts
  bids and states back to what they were at a point in time, built from Amazon's own
  record, so a rollback does not depend on having saved a plan beforehand.
"""

from __future__ import annotations

from collections.abc import Iterable, Iterator, Sequence
from datetime import UTC, date, datetime, timedelta
from typing import TYPE_CHECKING, Any

from amazon_ads.models import HistoryEvent
from amazon_ads.plans import ChangePlan, PlannedChange

if TYPE_CHECKING:
    from amazon_ads.client import ProfileClient

PATH = "/history"
MAX_PAGE = 200
MAX_IDS_PER_TYPE = 10  # eventTypeIds and parents accept at most 10 entries each
RETENTION = timedelta(days=90)

ENTITY_TYPES = ("CAMPAIGN", "AD_GROUP", "AD", "KEYWORD", "NEGATIVE_KEYWORD", "PRODUCT_TARGETING")

# History entity type + change type -> (plan kind, field) for rollback plans.
_ROLLBACK_FIELDS = {
    ("KEYWORD", "BID_AMOUNT"): ("sp.keywords", "bid"),
    ("KEYWORD", "STATUS"): ("sp.keywords", "state"),
    ("PRODUCT_TARGETING", "BID_AMOUNT"): ("sp.targets", "bid"),
    ("PRODUCT_TARGETING", "STATUS"): ("sp.targets", "state"),
    ("AD_GROUP", "BID_AMOUNT"): ("sp.ad_groups", "defaultBid"),
    ("AD_GROUP", "STATUS"): ("sp.ad_groups", "state"),
    ("CAMPAIGN", "STATUS"): ("sp.campaigns", "state"),
    ("AD", "STATUS"): ("sp.product_ads", "state"),
}


def to_epoch_ms(value: datetime | date) -> int:
    if isinstance(value, datetime):
        dt = value if value.tzinfo else value.replace(tzinfo=UTC)
    else:
        dt = datetime(value.year, value.month, value.day, tzinfo=UTC)
    return int(dt.timestamp() * 1000)


class History:
    def __init__(self, client: ProfileClient) -> None:
        self._client = client

    def events(
        self,
        *,
        since: datetime | date | None = None,
        until: datetime | date | None = None,
        entity_types: Iterable[str] = ENTITY_TYPES,
        entity_ids: Sequence[str | int] | None = None,
        campaign_ids: Sequence[str | int] | None = None,
        changes: Iterable[str] | None = None,
        newest_first: bool = True,
    ) -> Iterator[HistoryEvent]:
        """Yield change events, paging through Amazon's results.

        ``since`` defaults to 90 days ago (Amazon's limit) and ``until`` to now.
        ``entity_ids`` narrows to specific keyword, target, ad group or campaign ids;
        ``campaign_ids`` narrows to children of those campaigns. ``changes`` filters by
        change type, e.g. ``BID_AMOUNT``, ``BUDGET_AMOUNT``, ``STATUS``, ``CREATED``,
        ``PLACEMENT_GROUP``, ``PORTFOLIO_ID``, ``NAME``, ``START_DATE``, ``END_DATE``.
        """
        now = datetime.now(UTC)
        start = to_epoch_ms(since) if since else to_epoch_ms(now - RETENTION + timedelta(minutes=5))
        end = to_epoch_ms(until) if until else to_epoch_ms(now)
        types = list(entity_types)
        change_filter = [c.upper() for c in changes] if changes else None

        id_chunks: list[list[str] | None] = (
            [
                [str(i) for i in entity_ids[k : k + MAX_IDS_PER_TYPE]]
                for k in range(0, len(entity_ids), MAX_IDS_PER_TYPE)
            ]
            if entity_ids
            else [None]
        )
        parent_chunks: list[list[str] | None] = (
            [
                [str(i) for i in campaign_ids[k : k + MAX_IDS_PER_TYPE]]
                for k in range(0, len(campaign_ids), MAX_IDS_PER_TYPE)
            ]
            if campaign_ids
            else [None]
        )

        wanted_types = {t.upper() for t in types}
        for ids in id_chunks:
            for parents in parent_chunks:
                event_types: dict[str, Any] = {}
                for entity_type in types:
                    selector: dict[str, Any] = {}
                    if ids:
                        selector["eventTypeIds"] = ids
                    if parents:
                        selector["parents"] = [{"campaignId": p} for p in parents]
                    if not ids and not parents:
                        # Without ids or parent campaigns Amazon returns no events at
                        # all unless asked for the whole advertiser this way.
                        selector["parents"] = [{"useProfileIdAdvertiser": True}]
                    event_types[entity_type] = selector
                body = {
                    "eventTypes": event_types,
                    "fromDate": start,
                    "toDate": end,
                    "count": MAX_PAGE,
                    "sort": {"key": "DATE", "direction": "DESC" if newest_first else "ASC"},
                }
                # Amazon's per-type "filters" did not filter by change type when tested
                # (a STATUS filter returned CREATED events, and other entity types came
                # back too), so both are applied here instead.
                for event in self._pages(body):
                    if (event.entity_type or "").upper() not in wanted_types:
                        continue
                    if change_filter and (event.change_type or "").upper() not in change_filter:
                        continue
                    yield event

    def list(self, **kwargs: Any) -> list[HistoryEvent]:
        """:meth:`events` as a list."""
        return list(self.events(**kwargs))

    def value_at(
        self,
        entity_type: str,
        entity_id: str | int,
        change: str,
        at: datetime,
        *,
        events: Sequence[HistoryEvent] | None = None,
    ) -> str | None:
        """What ``change`` (e.g. ``"BID_AMOUNT"``) held on an entity at moment ``at``.

        Returns Amazon's string value, or ``None`` when no change to that field falls
        in the history window, which means the value then was the value now.
        """
        found = (
            events
            if events is not None
            else self.list(entity_types=[entity_type], entity_ids=[entity_id], changes=[change])
        )
        relevant = sorted(
            (
                e
                for e in found
                if str(e.entity_id) == str(entity_id)
                and (e.change_type or "").upper() == change.upper()
                and e.timestamp is not None
            ),
            key=lambda e: e.timestamp or 0,
        )
        if not relevant:
            return None
        at_ms = to_epoch_ms(at)
        before = [e for e in relevant if (e.timestamp or 0) <= at_ms]
        if before:
            return _as_str(before[-1].new_value)
        return _as_str(relevant[0].previous_value)

    def rollback_plan(
        self,
        since: datetime,
        *,
        entity_types: Iterable[str] = ("KEYWORD", "PRODUCT_TARGETING", "AD_GROUP", "CAMPAIGN"),
        changes: Iterable[str] = ("BID_AMOUNT", "STATUS"),
        campaign_ids: Sequence[str | int] | None = None,
        note: str | None = None,
    ) -> ChangePlan:
        """A plan restoring every bid and state changed since ``since`` to its value at
        that moment, from Amazon's change history.

        Entities changed several times are restored to the value before the *first*
        change. The plan's ``before`` values are the values after the latest change; use
        ``apply(check_drift=True)`` to catch anything changed after the plan was built.
        Change types without a known update field (budget amounts, names, dates) are
        listed in ``warnings``.
        """
        events = self.list(
            since=since,
            entity_types=entity_types,
            changes=changes,
            campaign_ids=campaign_ids,
            newest_first=False,
        )
        first: dict[tuple[str, str, str], HistoryEvent] = {}
        last: dict[tuple[str, str, str], HistoryEvent] = {}
        warnings: list[str] = []
        for event in events:
            key = (event.entity_type or "", str(event.entity_id), event.change_type or "")
            first.setdefault(key, event)
            last[key] = event

        by_entity: dict[tuple[str, str], dict[str, tuple[Any, Any]]] = {}
        labels: dict[tuple[str, str], str] = {}
        for key, event in first.items():
            entity_type, entity_id, change = key
            mapping = _ROLLBACK_FIELDS.get((entity_type, change))
            if mapping is None:
                warnings.append(f"{entity_type} {entity_id}: {change} change not restorable")
                continue
            kind, field = mapping
            old = _coerce(field, event.previous_value)
            new = _coerce(field, last[key].new_value)
            if old is None or old == new:
                continue
            by_entity.setdefault((kind, entity_id), {})[field] = (new, old)
            label = _label(last[key])
            if label:
                labels[(kind, entity_id)] = label

        plan_changes = [
            PlannedChange(
                kind=kind,
                id=entity_id,
                before={f: v[0] for f, v in fields.items()},
                after={f: v[1] for f, v in fields.items()},
                label=labels.get((kind, entity_id)),
            )
            for (kind, entity_id), fields in sorted(by_entity.items())
        ]
        return ChangePlan.new(
            profile_id=self._client.profile_id,
            country_code=self._client.country_code,
            changes=plan_changes,
            note=note or f"Restore bids and states to {since.isoformat()} from change history",
            warnings=warnings,
        )

    def _pages(self, body: dict[str, Any]) -> Iterator[HistoryEvent]:
        while True:
            response = self._client.request("POST", PATH, json=body, idempotent=True)
            payload = response.json() or {}
            for raw in payload.get("events") or []:
                yield HistoryEvent.model_validate(raw)
            token = payload.get("nextToken")
            if not token:
                return
            body = {**body, "nextToken": token}


def _label(event: HistoryEvent) -> str | None:
    meta = event.metadata or {}
    keyword = meta.get("keyword")
    if keyword:
        match = str(meta.get("keywordType") or "").replace("KEYWORD_", "")
        return f"{keyword} [{match}]" if match else str(keyword)
    expression = meta.get("targetingExpression")
    return str(expression) if expression else None


def _as_str(value: Any) -> str | None:
    return None if value is None else str(value)


def _coerce(field: str, value: Any) -> Any:
    """History values are strings; turn them into what an update payload expects."""
    if value is None or value == "":
        return None
    if field in {"bid", "defaultBid"}:
        try:
            return round(float(value), 2)
        except (TypeError, ValueError):
            return None
    if field == "state":
        return str(value).upper()
    return value
