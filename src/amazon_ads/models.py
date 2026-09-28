"""Typed models for the entities the library wraps.

Models accept Amazon's camelCase field names and Python snake_case alike, keep any field
Amazon adds that the model does not name yet (``extra="allow"``), and serialize back to
Amazon's spelling with :meth:`ApiModel.to_api`.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import BaseModel, ConfigDict
from pydantic.alias_generators import to_camel


class ApiModel(BaseModel):
    model_config = ConfigDict(
        alias_generator=to_camel,
        populate_by_name=True,
        extra="allow",
        use_enum_values=True,
    )

    def to_api(self, *, only: set[str] | None = None) -> dict[str, Any]:
        """Amazon's JSON for this model, with unset and ``None`` fields left out.

        ``only`` restricts the output to those API field names, which is how update
        payloads avoid sending fields Amazon treats as read-only.
        """
        data = self.model_dump(by_alias=True, exclude_none=True)
        if only is not None:
            data = {k: v for k, v in data.items() if k in only}
        return data


class State(StrEnum):
    ENABLED = "ENABLED"
    PAUSED = "PAUSED"
    ARCHIVED = "ARCHIVED"


class MatchType(StrEnum):
    EXACT = "EXACT"
    PHRASE = "PHRASE"
    BROAD = "BROAD"


class NegativeMatchType(StrEnum):
    NEGATIVE_EXACT = "NEGATIVE_EXACT"
    NEGATIVE_PHRASE = "NEGATIVE_PHRASE"


# --- Profiles -----------------------------------------------------------------------------


class AccountInfo(ApiModel):
    marketplace_string_id: str | None = None
    id: str | None = None
    type: str | None = None
    name: str | None = None
    sub_type: str | None = None
    valid_payment_method: bool | None = None


class Profile(ApiModel):
    profile_id: int
    country_code: str
    currency_code: str | None = None
    daily_budget: float | None = None
    timezone: str | None = None
    account_info: AccountInfo | None = None

    @property
    def label(self) -> str:
        name = self.account_info.name if self.account_info else None
        kind = self.account_info.type if self.account_info else None
        return f"{self.country_code} {self.profile_id} ({kind}, {name})"


# --- Sponsored Products -------------------------------------------------------------------


class Budget(ApiModel):
    budget: float
    budget_type: str = "DAILY"


class PlacementBidding(ApiModel):
    placement: str
    percentage: int


class DynamicBidding(ApiModel):
    strategy: str | None = None
    placement_bidding: list[PlacementBidding] | None = None


class Campaign(ApiModel):
    campaign_id: str | None = None
    portfolio_id: str | None = None
    name: str | None = None
    targeting_type: str | None = None
    state: State | None = None
    budget: Budget | None = None
    start_date: str | None = None
    end_date: str | None = None
    dynamic_bidding: DynamicBidding | None = None
    tags: dict[str, str] | None = None
    extended_data: dict[str, Any] | None = None


class AdGroup(ApiModel):
    ad_group_id: str | None = None
    campaign_id: str | None = None
    name: str | None = None
    state: State | None = None
    default_bid: float | None = None
    extended_data: dict[str, Any] | None = None


class Keyword(ApiModel):
    keyword_id: str | None = None
    campaign_id: str | None = None
    ad_group_id: str | None = None
    keyword_text: str | None = None
    match_type: MatchType | None = None
    state: State | None = None
    bid: float | None = None
    native_language_keyword: str | None = None
    native_language_locale: str | None = None
    extended_data: dict[str, Any] | None = None


class NegativeKeyword(ApiModel):
    keyword_id: str | None = None
    campaign_id: str | None = None
    ad_group_id: str | None = None
    keyword_text: str | None = None
    match_type: NegativeMatchType | None = None
    state: State | None = None
    extended_data: dict[str, Any] | None = None


class CampaignNegativeKeyword(ApiModel):
    keyword_id: str | None = None
    campaign_id: str | None = None
    keyword_text: str | None = None
    match_type: NegativeMatchType | None = None
    state: State | None = None
    extended_data: dict[str, Any] | None = None


class TargetingExpression(ApiModel):
    """One predicate of a product or auto target, e.g. ``{"type": "ASIN_SAME_AS",
    "value": "B0..."}``. Auto targets use types such as ``QUERY_HIGH_REL_MATCHES`` with no
    value."""

    type: str
    value: str | None = None


class Target(ApiModel):
    target_id: str | None = None
    campaign_id: str | None = None
    ad_group_id: str | None = None
    expression: list[TargetingExpression] | None = None
    resolved_expression: list[TargetingExpression] | None = None
    expression_type: str | None = None
    state: State | None = None
    bid: float | None = None
    extended_data: dict[str, Any] | None = None

    @property
    def asin(self) -> str | None:
        """The ASIN of a single-ASIN product target, else ``None``."""
        for expr in self.expression or []:
            if expr.type in {"ASIN_SAME_AS", "ASIN_EXPANDED_FROM"}:
                return expr.value
        return None


class NegativeTarget(ApiModel):
    target_id: str | None = None
    campaign_id: str | None = None
    ad_group_id: str | None = None
    expression: list[TargetingExpression] | None = None
    resolved_expression: list[TargetingExpression] | None = None
    state: State | None = None
    extended_data: dict[str, Any] | None = None


class CampaignNegativeTarget(ApiModel):
    target_id: str | None = None
    campaign_id: str | None = None
    expression: list[TargetingExpression] | None = None
    resolved_expression: list[TargetingExpression] | None = None
    state: State | None = None
    extended_data: dict[str, Any] | None = None


class ProductAd(ApiModel):
    ad_id: str | None = None
    campaign_id: str | None = None
    ad_group_id: str | None = None
    asin: str | None = None
    sku: str | None = None
    custom_text: str | None = None
    state: State | None = None
    extended_data: dict[str, Any] | None = None


class Portfolio(ApiModel):
    portfolio_id: str | None = None
    name: str | None = None
    state: str | None = None
    budget: dict[str, Any] | None = None
    budget_controls: dict[str, Any] | None = None
    in_budget: bool | None = None
    extended_data: dict[str, Any] | None = None


# --- Sponsored Brands ---------------------------------------------------------------------


class SbCampaign(ApiModel):
    """A Sponsored Brands campaign (v4). ``bidding`` holds ``bidOptimization`` and the
    placement premiums in ``bidAdjustmentsByPlacement``; send the whole object to change
    either. ``state`` is upper case (``ENABLED``)."""

    campaign_id: str | None = None
    portfolio_id: str | None = None
    name: str | None = None
    state: str | None = None
    budget: float | None = None
    budget_type: str | None = None
    bidding: dict[str, Any] | None = None
    start_date: str | None = None
    end_date: str | None = None
    goal: str | None = None
    cost_type: str | None = None
    tags: dict[str, str] | None = None
    extended_data: dict[str, Any] | None = None


class SbKeyword(ApiModel):
    """A Sponsored Brands keyword (v3). Ids are integers and ``state`` and ``match_type``
    are lower case (``enabled``, ``phrase``), unlike Sponsored Products."""

    keyword_id: int | None = None
    campaign_id: int | None = None
    ad_group_id: int | None = None
    keyword_text: str | None = None
    native_language_keyword: str | None = None
    match_type: str | None = None
    state: str | None = None
    bid: float | None = None


class SbNegativeKeyword(ApiModel):
    """A Sponsored Brands negative keyword (v3): ``negativeExact`` or ``negativePhrase``,
    and only ``enabled`` or ``archived`` (it cannot be paused)."""

    keyword_id: int | None = None
    campaign_id: int | None = None
    ad_group_id: int | None = None
    keyword_text: str | None = None
    match_type: str | None = None
    state: str | None = None


# --- Bid recommendations ------------------------------------------------------------------


class SuggestedBid(ApiModel):
    """Amazon's suggested bid range for one targeting expression.

    ``low``, ``median`` and ``high`` are ``None`` when Amazon returned no suggestion for
    that expression (common for product targets in some marketplaces).
    """

    expression_type: str
    value: str | None = None
    low: float | None = None
    median: float | None = None
    high: float | None = None
    theme: str | None = None
    raw: dict[str, Any] | None = None

    @property
    def priced(self) -> bool:
        return self.median is not None


# --- Change history -----------------------------------------------------------------------


class HistoryEvent(ApiModel):
    """One change from ``POST /history``: what changed, from what, to what, and when."""

    entity_type: str | None = None
    entity_id: str | None = None
    change_type: str | None = None
    previous_value: Any = None
    new_value: Any = None
    timestamp: int | None = None
    metadata: dict[str, Any] | None = None
