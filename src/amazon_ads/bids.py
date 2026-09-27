"""Suggested bids from Amazon's theme-based bid recommendations.

``POST /sp/targets/bid/recommendations`` behaves in ways the spec does not spell out, and
this module handles them:

* **One targeting family per request.** A request mixing keyword and product (ASIN)
  expressions fails as a whole with 422 "All targeting expressions should have the same
  targeting type", so expressions are grouped into keyword, product and auto requests.
* **Product targets use ``PAT_ASIN``,** not the ``ASIN_SAME_AS`` type a product target
  carries on the campaign side (that returns 422). :func:`expression_for_target` converts.
  ``ASIN_EXPANDED_FROM`` has no bid recommendation equivalent and is reported as
  unsupported rather than sent.
* **At most 100 expressions per request,** so longer lists are chunked.
* **Some marketplaces return no suggestion** for some expressions (Australia returned none
  for product targets in 2026). Those come back with ``low``, ``median`` and ``high`` set
  to ``None`` instead of being dropped, so a missing price is visible.
* **Several themes.** Alongside ``CONVERSION_OPPORTUNITIES`` Amazon can return seasonal
  themes such as ``BFCM_HOLIDAY`` or ``PRIME_DAY`` with different values. The default
  theme is ``CONVERSION_OPPORTUNITIES``; ask for another with ``theme=`` or get all with
  :meth:`BidRecommendations.for_ad_group_all_themes`.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from amazon_ads.models import Keyword, SuggestedBid, Target

if TYPE_CHECKING:
    from amazon_ads.client import ProfileClient

PATH = "/sp/targets/bid/recommendations"
MEDIA_TYPE = "application/vnd.spthemebasedbidrecommendation.v4+json"
MAX_EXPRESSIONS = 100
DEFAULT_THEME = "CONVERSION_OPPORTUNITIES"

KEYWORD_TYPES = {
    "EXACT": "KEYWORD_EXACT_MATCH",
    "PHRASE": "KEYWORD_PHRASE_MATCH",
    "BROAD": "KEYWORD_BROAD_MATCH",
}

# Campaign-side targeting expression type -> bid recommendation expression type.
TARGET_TYPES = {
    "ASIN_SAME_AS": "PAT_ASIN",
    "ASIN_CATEGORY_SAME_AS": "PAT_CATEGORY",
    "QUERY_HIGH_REL_MATCHES": "CLOSE_MATCH",
    "QUERY_BROAD_REL_MATCHES": "LOOSE_MATCH",
    "ASIN_SUBSTITUTE_RELATED": "SUBSTITUTES",
    "ASIN_ACCESSORY_RELATED": "COMPLEMENTS",
}

FAMILIES = {
    "KEYWORD_EXACT_MATCH": "keyword",
    "KEYWORD_PHRASE_MATCH": "keyword",
    "KEYWORD_BROAD_MATCH": "keyword",
    "KEYWORD_GROUP": "keyword_group",
    "PAT_ASIN": "product",
    "PAT_CATEGORY": "product",
    "PAT_CATEGORY_REFINEMENT": "product",
    "CLOSE_MATCH": "auto",
    "LOOSE_MATCH": "auto",
    "SUBSTITUTES": "auto",
    "COMPLEMENTS": "auto",
}


@dataclass(frozen=True)
class Expression:
    """A bid recommendation targeting expression, e.g. ``Expression("PAT_ASIN", "B0...")``."""

    type: str
    value: str | None = None

    def to_api(self) -> dict[str, Any]:
        return {"type": self.type, "value": self.value} if self.value else {"type": self.type}

    @property
    def key(self) -> tuple[str, str | None]:
        return (self.type, self.value.lower() if self.value else None)


def expression_for_keyword(keyword_text: str, match_type: str) -> Expression:
    try:
        return Expression(KEYWORD_TYPES[match_type.upper()], keyword_text)
    except KeyError:
        raise ValueError(f"No bid recommendation type for match type {match_type!r}") from None


def expression_for_target(target: Target) -> Expression | None:
    """The bid recommendation expression for a campaign-side target, or ``None`` when
    Amazon offers no recommendation for that kind of target (``ASIN_EXPANDED_FROM``,
    multi-predicate refinements)."""
    parts = target.expression or []
    if len(parts) != 1:
        return None
    mapped = TARGET_TYPES.get(parts[0].type)
    if mapped is None:
        return None
    return Expression(mapped, parts[0].value)


@dataclass
class BidRecommendationResult:
    """Suggestions in request order, plus anything that could not be asked about."""

    suggestions: list[SuggestedBid]
    unsupported: list[Any]

    def by_value(self) -> dict[tuple[str, str | None], SuggestedBid]:
        return {(s.expression_type, (s.value or "").lower() or None): s for s in self.suggestions}


class BidRecommendations:
    def __init__(self, client: ProfileClient) -> None:
        self._client = client

    def for_ad_group(
        self,
        campaign_id: str | int,
        ad_group_id: str | int,
        expressions: Iterable[Expression],
        *,
        theme: str = DEFAULT_THEME,
    ) -> list[SuggestedBid]:
        """Suggested bids for expressions in an existing ad group, in request order."""
        exprs = _dedupe(expressions)
        by_theme = self.for_ad_group_all_themes(campaign_id, ad_group_id, exprs)
        return by_theme.get(theme) or _empty(exprs, theme)

    def for_ad_group_all_themes(
        self,
        campaign_id: str | int,
        ad_group_id: str | int,
        expressions: Iterable[Expression],
    ) -> dict[str, list[SuggestedBid]]:
        """Suggestions for every theme Amazon returned, keyed by theme."""
        exprs = _dedupe(expressions)
        themes: dict[str, dict[tuple[str, str | None], SuggestedBid]] = {}
        for family_chunk in _family_chunks(exprs):
            body = {
                "recommendationType": "BIDS_FOR_EXISTING_AD_GROUP",
                "campaignId": str(campaign_id),
                "adGroupId": str(ad_group_id),
                "targetingExpressions": [e.to_api() for e in family_chunk],
            }
            for theme, suggestions in self._send(body).items():
                themes.setdefault(theme, {}).update({_key(s): s for s in suggestions})
        return {
            theme: [found.get(e.key) or _empty([e], theme)[0] for e in exprs]
            for theme, found in themes.items()
        }

    def for_new_ad_group(
        self,
        asins: Sequence[str],
        expressions: Iterable[Expression],
        *,
        strategy: str = "MANUAL",
        placement_adjustments: dict[str, int] | None = None,
        theme: str = DEFAULT_THEME,
    ) -> list[SuggestedBid]:
        """Suggested bids before an ad group exists, for the ASINs it would advertise.

        ``placement_adjustments`` maps ``PLACEMENT_TOP`` / ``PLACEMENT_PRODUCT_PAGE`` /
        ``PLACEMENT_REST_OF_SEARCH`` to a percentage.
        """
        exprs = _dedupe(expressions)
        bidding: dict[str, Any] = {"strategy": strategy}
        if placement_adjustments:
            bidding["adjustments"] = [
                {"predicate": k, "percentage": v} for k, v in placement_adjustments.items()
            ]
        found: dict[tuple[str, str | None], SuggestedBid] = {}
        for family_chunk in _family_chunks(exprs):
            body = {
                "recommendationType": "BIDS_FOR_NEW_AD_GROUP",
                "asins": list(asins)[:50],
                "bidding": bidding,
                "targetingExpressions": [e.to_api() for e in family_chunk],
            }
            found.update({_key(s): s for s in self._send(body).get(theme, [])})
        return [found.get(e.key) or _empty([e], theme)[0] for e in exprs]

    def for_keywords(
        self, keywords: Iterable[Keyword], *, theme: str = DEFAULT_THEME
    ) -> dict[str, SuggestedBid]:
        """Suggestions for existing keywords, keyed by keyword id. Keywords are grouped by
        ad group, since recommendations are asked per ad group."""
        groups: dict[tuple[str, str], list[tuple[str, Expression]]] = {}
        for kw in keywords:
            if not (kw.keyword_id and kw.campaign_id and kw.ad_group_id and kw.keyword_text):
                continue
            expr = expression_for_keyword(kw.keyword_text, str(kw.match_type))
            groups.setdefault((kw.campaign_id, kw.ad_group_id), []).append((kw.keyword_id, expr))
        return self._by_id(groups, theme)

    def for_targets(
        self, targets: Iterable[Target], *, theme: str = DEFAULT_THEME
    ) -> BidRecommendationResult:
        """Suggestions for existing product and auto targets, in input order.

        Targets Amazon offers no recommendation for (``ASIN_EXPANDED_FROM`` and the like)
        are returned in ``unsupported`` instead of failing the request.
        """
        targets = list(targets)
        groups: dict[tuple[str, str], list[tuple[str, Expression]]] = {}
        unsupported: list[Any] = []
        for target in targets:
            expr = expression_for_target(target)
            if expr is None or not (target.target_id and target.campaign_id and target.ad_group_id):
                unsupported.append(target)
                continue
            groups.setdefault((target.campaign_id, target.ad_group_id), []).append(
                (target.target_id, expr)
            )
        by_id = self._by_id(groups, theme)
        ordered = [by_id[t.target_id] for t in targets if t.target_id in by_id]
        return BidRecommendationResult(suggestions=ordered, unsupported=unsupported)

    def _by_id(
        self, groups: dict[tuple[str, str], list[tuple[str, Expression]]], theme: str
    ) -> dict[str, SuggestedBid]:
        result: dict[str, SuggestedBid] = {}
        for (campaign_id, ad_group_id), pairs in groups.items():
            suggestions = self.for_ad_group(
                campaign_id, ad_group_id, [e for _, e in pairs], theme=theme
            )
            lookup = {_key(s): s for s in suggestions}
            for entity_id, expr in pairs:
                result[entity_id] = lookup.get(expr.key) or _empty([expr], theme)[0]
        return result

    def _send(self, body: dict[str, Any]) -> dict[str, list[SuggestedBid]]:
        response = self._client.request(
            "POST", PATH, json=body, content_type=MEDIA_TYPE, idempotent=True
        )
        return parse_recommendations(response.json())


def parse_recommendations(payload: Any) -> dict[str, list[SuggestedBid]]:
    """Read a v4 response into suggestions keyed by theme.

    ``bidValues`` holds three entries, low to high; fewer means Amazon had no range.
    """
    themes: dict[str, list[SuggestedBid]] = {}
    for block in (payload or {}).get("bidRecommendations") or []:
        theme = block.get("theme") or DEFAULT_THEME
        for entry in block.get("bidRecommendationsForTargetingExpressions") or []:
            expr = entry.get("targetingExpression") or {}
            values = sorted(
                float(v["suggestedBid"])
                for v in entry.get("bidValues") or []
                if isinstance(v, dict) and v.get("suggestedBid") is not None
            )
            low = median = high = None
            if len(values) >= 3:
                low, median, high = values[0], values[len(values) // 2], values[-1]
            elif len(values) == 1:
                median = values[0]
            themes.setdefault(theme, []).append(
                SuggestedBid(
                    expression_type=str(expr.get("type")),
                    value=expr.get("value"),
                    low=low,
                    median=median,
                    high=high,
                    theme=theme,
                    raw=entry,
                )
            )
    return themes


def _key(s: SuggestedBid) -> tuple[str, str | None]:
    return (s.expression_type, s.value.lower() if s.value else None)


def _dedupe(expressions: Iterable[Expression]) -> list[Expression]:
    seen: dict[tuple[str, str | None], Expression] = {}
    for e in expressions:
        if e.type not in FAMILIES:
            raise ValueError(f"Unsupported bid recommendation expression type {e.type!r}")
        seen.setdefault(e.key, e)
    return list(seen.values())


def _family_chunks(expressions: list[Expression]) -> list[list[Expression]]:
    families: dict[str, list[Expression]] = {}
    for e in expressions:
        families.setdefault(FAMILIES[e.type], []).append(e)
    chunks = []
    for group in families.values():
        for start in range(0, len(group), MAX_EXPRESSIONS):
            chunks.append(group[start : start + MAX_EXPRESSIONS])
    return chunks


def _empty(expressions: list[Expression], theme: str) -> list[SuggestedBid]:
    return [SuggestedBid(expression_type=e.type, value=e.value, theme=theme) for e in expressions]
