from __future__ import annotations

import pytest
import respx
from httpx import Response

from amazon_ads import ProfileClient, ServerError, catalog
from amazon_ads.transport import READ_ONLY_POST_SUFFIXES
from tests.conftest import NA


def test_catalog_covers_every_published_api() -> None:
    apis = catalog.apis()
    assert len(apis) >= 200
    for slug in (
        "sponsored-products",
        "sponsored-brands-v4",
        "sponsored-display",
        "reporting",
        "history",
        "profiles",
        "dsp-campaigns",
    ):
        assert slug in apis
    assert len(catalog.operations()) >= 1000


def test_every_operation_can_be_sent() -> None:
    for op in catalog.operations():
        assert op.method in {"GET", "POST", "PUT", "DELETE", "PATCH"}, op.id
        assert op.path.startswith("/"), op.id
        for name in op.path_params:
            assert "{" + name + "}" in op.path, op.id


def test_sp_media_types_match_what_the_typed_layer_sends() -> None:
    from amazon_ads.sp import ALL_SPECS

    for spec in ALL_SPECS:
        op = next(
            o
            for o in catalog.operations(
                "sponsored-products" if spec.kind != "portfolios" else "portfolios"
            )
            if o.path == f"{spec.path}/list"
        )
        assert op.content_type == spec.media_type, spec.kind


@pytest.mark.parametrize(
    ("operation", "read_only"),
    [
        pytest.param("sponsored-products:ListSponsoredProductsKeywords", True, id="list-suffix"),
        pytest.param("sponsored-products:CreateSponsoredProductsKeywords", False, id="create"),
        pytest.param("sponsored-products:UpdateSponsoredProductsKeywords", False, id="update"),
        pytest.param("sponsored-products:DeleteSponsoredProductsKeywords", False, id="delete"),
        pytest.param("profiles:listProfiles", True, id="get-method"),
        # Writes that a substring test on the last path segment called read-only, so the
        # MCP server ran them without a plan and the transport retried them after a 5xx.
        pytest.param(
            "sponsored-products:CreateSponsoredProductsTargetingClauses",
            False,
            id="sp-create-targets-get-inside-targets",
        ),
        pytest.param(
            "sponsored-brands-v3:createTargets", False, id="sb-create-targets-get-inside-targets"
        ),
        pytest.param(
            "sponsored-brands-v3:createNegativeTargets",
            False,
            id="sb-create-negative-targets-get-inside-negativeTargets",
        ),
        pytest.param(
            "amazon-ads-dspapi-targets-v1:CreateDspTargetsV1",
            False,
            id="dsp-create-targets-get-inside-targets",
        ),
        pytest.param(
            "media-planning-api:CreateTargetsV1",
            False,
            id="media-plan-create-targets-get-inside-targets",
        ),
        pytest.param(
            "v1-sp-targets:SPCreateTarget",
            False,
            id="unified-create-targets-get-inside-targets",
        ),
        pytest.param(
            "ad-group-targeting-third-party:DspPostAdGroup3PTargeting",
            False,
            id="dsp-post-targets-not-named-create-get-inside-targets",
        ),
        pytest.param(
            "sponsored-products:CreateBudgetRulesForSPCampaigns",
            False,
            id="sp-create-budget-rules-get-inside-budgetRules",
        ),
        pytest.param(
            "sponsored-products:BulkBudgetRulesAssociationForSP",
            False,
            id="sp-associate-budget-rules-get-inside-budgetRulesAssociation",
        ),
        pytest.param(
            "sponsored-products:CreateTargetPromotionGroups",
            False,
            id="sp-create-promotion-groups-get-inside-targetPromotionGroups",
        ),
        pytest.param(
            "media-planning-api:CreateBudgetAllocationsV1",
            False,
            id="media-plan-create-allocations-get-inside-budgetAllocations",
        ),
        pytest.param(
            "measurement:CreateDSPAudienceResearchStudy",
            False,
            id="create-study-search-inside-audienceResearch",
        ),
        pytest.param(
            "mcp-campaign-management:update_target_bid",
            False,
            id="mcp-update-bid-get-inside-update_target_bid",
        ),
        pytest.param(
            "rule-based-audiences:createQueryBasedAudience",
            False,
            id="create-on-a-query-suffix-path",
        ),
        # Reads that must stay read-only, so call_operation keeps serving them.
        pytest.param(
            "sponsored-products:GetThemeBasedBidRecommendationForAdGroup_v1",
            True,
            id="bid-recommendations-under-targets",
        ),
        pytest.param("history:getHistory", True, id="history-path"),
        pytest.param(
            "advertising-billing:GetBillingInvoiceSummaries", True, id="invoice-summaries-list"
        ),
        pytest.param(
            "mcp-amc:get_workflow_execution_status", True, id="execution-status-get-prefix"
        ),
        pytest.param(
            "mcp-advertiser-product-group-eligibility:query", True, id="query-suffix-read"
        ),
        pytest.param(
            "v1-sp-targets:SPQueryTarget", True, id="unified-query-segment-before-targets"
        ),
        pytest.param(
            "sponsored-brands-v4:SBOptimizationRecommendation",
            True,
            id="recommendations-segment-before-the-last",
        ),
        pytest.param(
            "sponsored-brands-v4:SBTargetingGetTargetableASINCounts",
            True,
            id="transport-suffix-targets-products-count",
        ),
    ],
)
def test_read_only_classification(operation: str, read_only: bool) -> None:
    assert catalog.get(operation).read_only is read_only


def test_no_catalogued_create_update_delete_or_archive_is_read_only() -> None:
    # Thirty such operations once counted as read-only, which let the MCP server send
    # them without approval. A new spec or a rule change must not bring that back.
    leaked = [
        op.id
        for op in catalog.operations()
        if op.operation_id.lower().startswith(catalog.WRITE_WORDS) and op.read_only
    ]
    assert leaked == []


def test_catalog_counts_every_transport_read_only_path_as_read_only() -> None:
    # The transport retries a raw POST to these paths after a 5xx. If the catalog called
    # one a write, the same request would be retried through request() but not through
    # call(), and the two answers to "is this safe to send twice" would disagree.
    disagree = [
        op.id
        for op in catalog.operations()
        if op.method == "POST"
        and op.path.rstrip("/").endswith(READ_ONLY_POST_SUFFIXES)
        and not op.read_only
    ]
    assert disagree == []


def test_lookup_by_bare_id_and_search() -> None:
    assert catalog.get("ListSponsoredProductsKeywords").api == "sponsored-products"
    assert any(op.path == "/sp/keywords/list" for op in catalog.search("keywords list"))
    with pytest.raises(KeyError):
        catalog.get("NoSuchOperation")


def test_call_sends_the_catalogued_method_path_and_types(
    api: respx.MockRouter, us: ProfileClient
) -> None:
    route = api.get(f"{NA}/reporting/reports/abc").mock(
        return_value=Response(200, json={"reportId": "abc", "status": "PENDING"})
    )
    out = us.call("reporting:getAsyncReport", path_params={"reportId": "abc"})
    assert out["status"] == "PENDING"
    assert (
        route.calls.last.request.headers["Accept"]
        == "application/vnd.getasyncreportresponse.v3+json"
    )


def test_call_does_not_retry_a_create_after_a_server_error(
    api: respx.MockRouter, us: ProfileClient
) -> None:
    # call() retries only read-only operations after a 5xx. POST /sp/targets once
    # counted as read-only ("get" is a substring of "targets"), so a create whose 503
    # hid a success was sent again and could add every target twice.
    route = api.post(f"{NA}/sp/targets").mock(return_value=Response(503))
    with pytest.raises(ServerError):
        us.call(
            "sponsored-products:CreateSponsoredProductsTargetingClauses",
            {"targetingClauses": [{"campaignId": "1", "adGroupId": "2", "bid": 0.5}]},
        )
    assert route.call_count == 1


def test_call_refuses_missing_path_parameters(us: ProfileClient) -> None:
    with pytest.raises(ValueError, match="reportId"):
        us.call("reporting:getAsyncReport")
