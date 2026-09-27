"""Sponsored Products v3 campaign management.

Field names, media types, filter names and batch limits below are taken from Amazon's
Sponsored Products v3 OpenAPI spec (``SponsoredProducts_prod_3p.json``).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from amazon_ads.models import (
    AdGroup,
    Campaign,
    CampaignNegativeKeyword,
    CampaignNegativeTarget,
    Keyword,
    NegativeKeyword,
    NegativeTarget,
    Portfolio,
    ProductAd,
    Target,
)
from amazon_ads.sp.resource import EntitySpec, SpResource

if TYPE_CHECKING:
    from amazon_ads.client import ProfileClient

CAMPAIGNS = EntitySpec(
    kind="sp.campaigns",
    path="/sp/campaigns",
    media_type="application/vnd.spCampaign.v3+json",
    collection_key="campaigns",
    id_field="campaignId",
    id_filter="campaignIdFilter",
    model=Campaign,
    updatable=frozenset(
        {
            "campaignId",
            "name",
            "state",
            "budget",
            "startDate",
            "endDate",
            "dynamicBidding",
            "portfolioId",
            "tags",
            "targetingType",
            "siteRestrictions",
            "offAmazonSettings",
        }
    ),
)

AD_GROUPS = EntitySpec(
    kind="sp.ad_groups",
    path="/sp/adGroups",
    media_type="application/vnd.spAdGroup.v3+json",
    collection_key="adGroups",
    id_field="adGroupId",
    id_filter="adGroupIdFilter",
    model=AdGroup,
    updatable=frozenset({"adGroupId", "name", "state", "defaultBid"}),
)

KEYWORDS = EntitySpec(
    kind="sp.keywords",
    path="/sp/keywords",
    media_type="application/vnd.spKeyword.v3+json",
    collection_key="keywords",
    id_field="keywordId",
    id_filter="keywordIdFilter",
    model=Keyword,
    updatable=frozenset({"keywordId", "state", "bid"}),
)

TARGETS = EntitySpec(
    kind="sp.targets",
    path="/sp/targets",
    media_type="application/vnd.spTargetingClause.v3+json",
    collection_key="targetingClauses",
    id_field="targetId",
    id_filter="targetIdFilter",
    model=Target,
    updatable=frozenset({"targetId", "state", "bid", "expression", "expressionType"}),
)

NEGATIVE_KEYWORDS = EntitySpec(
    kind="sp.negative_keywords",
    path="/sp/negativeKeywords",
    media_type="application/vnd.spNegativeKeyword.v3+json",
    collection_key="negativeKeywords",
    id_field="keywordId",
    id_filter="negativeKeywordIdFilter",
    result_id_field="negativeKeywordId",
    model=NegativeKeyword,
    updatable=frozenset({"keywordId", "state"}),
)

NEGATIVE_TARGETS = EntitySpec(
    kind="sp.negative_targets",
    path="/sp/negativeTargets",
    media_type="application/vnd.spNegativeTargetingClause.v3+json",
    collection_key="negativeTargetingClauses",
    id_field="targetId",
    id_filter="negativeTargetIdFilter",
    model=NegativeTarget,
    updatable=frozenset({"targetId", "state", "expression"}),
)

CAMPAIGN_NEGATIVE_KEYWORDS = EntitySpec(
    kind="sp.campaign_negative_keywords",
    path="/sp/campaignNegativeKeywords",
    media_type="application/vnd.spCampaignNegativeKeyword.v3+json",
    collection_key="campaignNegativeKeywords",
    id_field="keywordId",
    id_filter="campaignNegativeKeywordIdFilter",
    result_id_field="campaignNegativeKeywordId",
    model=CampaignNegativeKeyword,
    updatable=frozenset({"keywordId", "state"}),
)

CAMPAIGN_NEGATIVE_TARGETS = EntitySpec(
    kind="sp.campaign_negative_targets",
    path="/sp/campaignNegativeTargets",
    media_type="application/vnd.spCampaignNegativeTargetingClause.v3+json",
    collection_key="campaignNegativeTargetingClauses",
    id_field="targetId",
    id_filter="campaignNegativeTargetIdFilter",
    result_id_field="campaignNegativeTargetingClauseId",
    model=CampaignNegativeTarget,
    updatable=frozenset({"targetId", "state", "expression"}),
)

PRODUCT_ADS = EntitySpec(
    kind="sp.product_ads",
    path="/sp/productAds",
    media_type="application/vnd.spProductAd.v3+json",
    collection_key="productAds",
    id_field="adId",
    id_filter="adIdFilter",
    model=ProductAd,
    updatable=frozenset({"adId", "state"}),
)

PORTFOLIOS = EntitySpec(
    kind="portfolios",
    path="/portfolios",
    media_type="application/vnd.spPortfolio.v3+json",
    collection_key="portfolios",
    id_field="portfolioId",
    id_filter="portfolioIdFilter",
    model=Portfolio,
    updatable=frozenset({"portfolioId", "name", "state", "budget", "budgetControls"}),
    # Amazon's portfolio stateFilter takes exactly one state.
    default_states=None,
)

ALL_SPECS: tuple[EntitySpec, ...] = (  # type: ignore[type-arg]
    CAMPAIGNS,
    AD_GROUPS,
    KEYWORDS,
    TARGETS,
    NEGATIVE_KEYWORDS,
    NEGATIVE_TARGETS,
    CAMPAIGN_NEGATIVE_KEYWORDS,
    CAMPAIGN_NEGATIVE_TARGETS,
    PRODUCT_ADS,
    PORTFOLIOS,
)


class SponsoredProducts:
    """``client.sp``: one :class:`SpResource` per Sponsored Products entity."""

    def __init__(self, client: ProfileClient) -> None:
        self.campaigns = SpResource(client, CAMPAIGNS)
        self.ad_groups = SpResource(client, AD_GROUPS)
        self.keywords = SpResource(client, KEYWORDS)
        self.targets = SpResource(client, TARGETS)
        self.negative_keywords = SpResource(client, NEGATIVE_KEYWORDS)
        self.negative_targets = SpResource(client, NEGATIVE_TARGETS)
        self.campaign_negative_keywords = SpResource(client, CAMPAIGN_NEGATIVE_KEYWORDS)
        self.campaign_negative_targets = SpResource(client, CAMPAIGN_NEGATIVE_TARGETS)
        self.product_ads = SpResource(client, PRODUCT_ADS)


__all__ = ["ALL_SPECS", "EntitySpec", "SpResource", "SponsoredProducts"]
