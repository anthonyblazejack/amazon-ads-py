"""Sponsored Brands campaign management: campaigns (v4), keywords and negative keywords (v3).

Sponsored Brands campaigns (``/sb/v4/campaigns``) follow the same list, update and 207
contract as Sponsored Products v3, so they reuse :class:`~amazon_ads.sp.resource.SpResource`
with a smaller batch and id-filter limit (10, from the v4 spec).

Sponsored Brands keywords and negative keywords are still on the older v3 API, which works
differently, and :class:`SbV3Resource` adapts it to the same interface so change plans
treat both products alike:

* listing is ``GET`` with comma-delimited query filters and ``startIndex`` / ``count`` paging,
* create and update send a bare JSON array of at most 100 items and answer 207 with a
  list of ``{"code": "SUCCESS", "keywordId": ...}`` in request order,
* an update must carry the keyword's ``adGroupId`` and ``campaignId`` as well as its id,
* archiving is ``DELETE /sb/keywords/{keywordId}``, one keyword per request,
* ids are integers and states are lower case (``enabled``, ``paused``, ``archived``).
"""

from __future__ import annotations

import builtins
from collections.abc import Iterable, Iterator, Mapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from amazon_ads.batch import BatchResult
from amazon_ads.errors import NotFoundError
from amazon_ads.models import SbCampaign, SbKeyword, SbNegativeKeyword
from amazon_ads.sp.resource import (
    UNSET,
    EntityInput,
    EntitySpec,
    SpResource,
    _chunks,
    _to_payload,
    _Unset,
)

if TYPE_CHECKING:
    from amazon_ads.client import ProfileClient

CAMPAIGNS = EntitySpec(
    kind="sb.campaigns",
    path="/sb/v4/campaigns",
    media_type="application/vnd.sbcampaignresource.v4+json",
    collection_key="campaigns",
    id_field="campaignId",
    id_filter="campaignIdFilter",
    model=SbCampaign,
    updatable=frozenset(
        {
            "campaignId",
            "name",
            "state",
            "budget",
            "bidding",
            "startDate",
            "endDate",
            "portfolioId",
            "tags",
        }
    ),
    # The v4 spec caps an update at 10 campaigns and an id filter at 10 ids.
    max_batch=10,
    max_filter_ids=10,
)


@dataclass(frozen=True)
class SbV3Spec(EntitySpec[Any]):
    list_accept: str = ""
    write_accept: str = "application/vnd.sbkeywordresponse.v3+json"
    # Every state a "list everything" read asks for.
    all_states: tuple[str, ...] = ("enabled", "paused", "archived")
    # Negative keywords take a single state per request; keywords take a comma list.
    single_state_filter: bool = False
    page_size: int = 500


KEYWORDS = SbV3Spec(
    kind="sb.keywords",
    path="/sb/keywords",
    media_type="application/json",
    collection_key="keywords",
    id_field="keywordId",
    id_filter="keywordIdFilter",
    model=SbKeyword,
    updatable=frozenset({"keywordId", "state", "bid"}),
    max_batch=100,
    max_filter_ids=100,
    default_states=("enabled", "paused"),
    list_accept="application/vnd.sbkeyword.v3.2+json",
)

NEGATIVE_KEYWORDS = SbV3Spec(
    kind="sb.negative_keywords",
    path="/sb/negativeKeywords",
    media_type="application/json",
    collection_key="negativeKeywords",
    id_field="keywordId",
    id_filter="keywordIdFilter",
    model=SbNegativeKeyword,
    updatable=frozenset({"keywordId", "state"}),
    max_batch=100,
    max_filter_ids=100,
    default_states=("enabled",),
    list_accept="application/vnd.sbnegativekeyword.v3.2+json",
    all_states=("enabled", "archived"),
    single_state_filter=True,
)

# Parent ids an SB v3 update must repeat alongside the keyword id.
_PARENT_FIELDS = ("adGroupId", "campaignId")
_INT_FIELDS = ("keywordId", "adGroupId", "campaignId")


class SbV3Resource(SpResource[Any]):
    """Sponsored Brands v3 keywords or negative keywords behind the SP resource interface."""

    spec: SbV3Spec

    def __init__(self, client: ProfileClient, spec: SbV3Spec) -> None:
        super().__init__(client, spec)

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
    ) -> Iterator[Any]:
        """Yield matching keywords. ``states`` defaults to the spec's default states;
        ``None`` asks for every state. ``filters`` adds query parameters (for example
        ``{"matchTypeFilter": "exact"}``)."""
        if name is not None or extended:
            raise ValueError("Sponsored Brands keywords have no name filter or extended data")
        if isinstance(states, _Unset):
            states = self.spec.default_states
        wanted = [s.lower() for s in states] if states is not None else list(self.spec.all_states)
        base: dict[str, Any] = dict(filters or {})
        if campaign_ids is not None:
            base["campaignIdFilter"] = ",".join(str(i) for i in campaign_ids)
        if ad_group_ids is not None:
            base["adGroupIdFilter"] = ",".join(str(i) for i in ad_group_ids)
        id_chunks: list[list[str] | None] = (
            [list(c) for c in _chunks([str(i) for i in ids], self.spec.max_filter_ids)]
            if ids is not None
            else [None]
        )
        state_groups = [[s] for s in wanted] if self.spec.single_state_filter else [wanted]
        count = page_size or self.spec.page_size
        for id_chunk in id_chunks:
            if id_chunk is not None and not id_chunk:
                continue
            for group in state_groups:
                params = {**base, "stateFilter": ",".join(group), "count": count}
                if id_chunk is not None:
                    params["keywordIdFilter"] = ",".join(id_chunk)
                start = 0
                while True:
                    response = self._client.request(
                        "GET",
                        self.spec.path,
                        params={**params, "startIndex": start},
                        accept=self.spec.list_accept,
                    )
                    rows = response.json() or []
                    for raw in rows:
                        yield self.spec.model.model_validate(raw)
                    if len(rows) < count:
                        break
                    start += len(rows)

    # --- writes -------------------------------------------------------------------------

    def create(self, items: Sequence[EntityInput]) -> BatchResult[dict[str, Any]]:
        payloads = [_normalize(_to_payload(item)) for item in items]
        return self._write("POST", self.spec.path, payloads)

    def update(self, items: Sequence[EntityInput]) -> BatchResult[dict[str, Any]]:
        """Update keywords. Each item needs ``keywordId`` plus the fields to change; the
        ``adGroupId`` and ``campaignId`` Amazon also requires are read from the account
        when the item does not carry them."""
        payloads = [self._update_payload(item) for item in items]
        need = [str(p["keywordId"]) for p in payloads if any(f not in p for f in _PARENT_FIELDS)]
        current = self.get_many(need) if need else {}
        for payload in payloads:
            entity = current.get(str(payload["keywordId"]))
            if entity is not None:
                data = entity.to_api()
                for parent in _PARENT_FIELDS:
                    if parent not in payload and data.get(parent) is not None:
                        payload[parent] = data[parent]
        return self._write("PUT", self.spec.path, [_normalize(p) for p in payloads])

    def delete(self, ids: Sequence[str | int]) -> BatchResult[dict[str, Any]]:
        """Archive keywords, one ``DELETE`` per id. Archived keywords cannot come back."""
        items = [{"keywordId": int(i)} for i in ids]
        return self._send(items, self._delete_chunk, repeatable=True)

    # --- internals ----------------------------------------------------------------------

    def _delete_chunk(self, chunk: builtins.list[dict[str, Any]]) -> builtins.list[dict[str, Any]]:
        answers = []
        for item in chunk:
            try:
                answers.append(
                    self._client.request(
                        "DELETE",
                        f"{self.spec.path}/{item['keywordId']}",
                        accept=self.spec.write_accept,
                    ).json()
                )
            except NotFoundError as exc:
                answers.append(
                    {"keywordId": item["keywordId"], "code": "NOT_FOUND", "description": str(exc)}
                )
        return answers

    def _update_payload(self, item: EntityInput) -> dict[str, Any]:
        payload = super()._update_payload(item)
        raw = _to_payload(item)
        for parent in _PARENT_FIELDS:
            if parent in raw:
                payload[parent] = raw[parent]
        return _normalize(payload)

    def _write(
        self, method: str, path: str, payloads: builtins.list[dict[str, Any]]
    ) -> BatchResult[dict[str, Any]]:
        return self._send(
            payloads,
            lambda chunk: self._client.request(
                method,
                path,
                json=chunk,
                content_type="application/json",
                accept=self.spec.write_accept,
            ).json(),
            repeatable=method != "POST",
        )


def _normalize(payload: dict[str, Any]) -> dict[str, Any]:
    """SB v3 wants integer ids and lower-case states."""
    out = dict(payload)
    for key in _INT_FIELDS:
        if out.get(key) is not None:
            out[key] = int(out[key])
    if isinstance(out.get("state"), str):
        out["state"] = out["state"].lower()
    return out


class SponsoredBrands:
    """Sponsored Brands resources for one profile: ``campaigns`` (v4), ``keywords`` and
    ``negative_keywords`` (v3)."""

    def __init__(self, client: ProfileClient) -> None:
        self.campaigns: SpResource[SbCampaign] = SpResource(client, CAMPAIGNS)
        self.keywords: SbV3Resource = SbV3Resource(client, KEYWORDS)
        self.negative_keywords: SbV3Resource = SbV3Resource(client, NEGATIVE_KEYWORDS)


def resource_for(client: ProfileClient, kind: str) -> SpResource[Any] | None:
    """The Sponsored Brands resource behind a change plan's ``kind``, or ``None``."""
    if kind == CAMPAIGNS.kind:
        return SpResource(client, CAMPAIGNS)
    for spec in (KEYWORDS, NEGATIVE_KEYWORDS):
        if spec.kind == kind:
            return SbV3Resource(client, spec)
    return None


ALL_SPECS: tuple[EntitySpec[Any], ...] = (CAMPAIGNS, KEYWORDS, NEGATIVE_KEYWORDS)

__all__ = ["ALL_SPECS", "SbV3Resource", "SponsoredBrands", "resource_for"]
