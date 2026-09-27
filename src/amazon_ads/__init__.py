"""amazon-ads-py: a typed client, CLI and MCP server for the Amazon Ads API."""

from amazon_ads._version import __version__
from amazon_ads.batch import BatchError, BatchResult, BatchSuccess
from amazon_ads.bids import Expression
from amazon_ads.client import AmazonAds, ProfileClient
from amazon_ads.config import Credentials
from amazon_ads.errors import (
    AmazonAdsError,
    ApiError,
    AuthError,
    BadRequestError,
    ConfigurationError,
    ForbiddenError,
    NotFoundError,
    PartialFailureError,
    PlanDriftError,
    ReportFailedError,
    ServerError,
    ThrottledError,
    UnauthorizedError,
    UnprocessableError,
)
from amazon_ads.models import (
    AdGroup,
    Campaign,
    CampaignNegativeKeyword,
    CampaignNegativeTarget,
    HistoryEvent,
    Keyword,
    NegativeKeyword,
    NegativeTarget,
    Portfolio,
    ProductAd,
    Profile,
    SuggestedBid,
    Target,
    TargetingExpression,
)
from amazon_ads.plans import ChangePlan, PlannedChange, PlanResult
from amazon_ads.regions import Region
from amazon_ads.reports import PRESETS as REPORT_PRESETS
from amazon_ads.reports import ReportResult
from amazon_ads.transport import RetryPolicy
from amazon_ads.warehouse import Warehouse

__all__ = [
    "REPORT_PRESETS",
    "AdGroup",
    "AmazonAds",
    "AmazonAdsError",
    "ApiError",
    "AuthError",
    "BadRequestError",
    "BatchError",
    "BatchResult",
    "BatchSuccess",
    "Campaign",
    "CampaignNegativeKeyword",
    "CampaignNegativeTarget",
    "ChangePlan",
    "ConfigurationError",
    "Credentials",
    "Expression",
    "ForbiddenError",
    "HistoryEvent",
    "Keyword",
    "NegativeKeyword",
    "NegativeTarget",
    "NotFoundError",
    "PartialFailureError",
    "PlanDriftError",
    "PlanResult",
    "PlannedChange",
    "Portfolio",
    "ProductAd",
    "Profile",
    "ProfileClient",
    "Region",
    "ReportFailedError",
    "ReportResult",
    "RetryPolicy",
    "ServerError",
    "SuggestedBid",
    "Target",
    "TargetingExpression",
    "ThrottledError",
    "UnauthorizedError",
    "UnprocessableError",
    "Warehouse",
    "__version__",
]
