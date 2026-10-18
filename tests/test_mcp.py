from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest
import respx
from httpx import Response

from amazon_ads import AmazonAds, ProfileClient, UnsupportedOperationError
from amazon_ads.mcp.server import AdsTools, aggregate, build_server
from amazon_ads.models import Profile
from tests.conftest import NA, US_PROFILE


@pytest.fixture
def tools(ads: AmazonAds, tmp_path) -> AdsTools:  # type: ignore[no-untyped-def]
    ads._profiles = [Profile(profile_id=US_PROFILE, country_code="US", currency_code="USD")]
    return AdsTools(ads, plan_dir=tmp_path)


def keyword_list(bid: float) -> Response:
    return Response(
        200,
        json={
            "keywords": [{"keywordId": "1", "keywordText": "a", "matchType": "EXACT", "bid": bid}]
        },
    )


def test_plan_then_apply_with_fingerprint(api: respx.MockRouter, tools: AdsTools) -> None:
    api.post(f"{NA}/sp/keywords/list").mock(return_value=keyword_list(0.5))
    update = api.put(f"{NA}/sp/keywords").mock(
        return_value=Response(207, json={"keywords": {"success": [{"index": 0, "keywordId": "1"}]}})
    )
    planned = tools.plan_update("US", "keywords", [{"keywordId": "1", "bid": 0.6}], note="test")
    assert "| update | sp.keywords | 1 | a [EXACT] | bid | 0.5 | 0.6 |" in planned["table"]
    assert not update.called

    applied = tools.apply_plan(planned["plan_id"], planned["fingerprint"])
    assert applied["ok"] is True
    assert json.loads(update.calls.last.request.content) == {
        "keywords": [{"keywordId": "1", "bid": 0.6}]
    }

    rollback = tools.get_plan(applied["rollback_plan_id"])
    assert "| bid | 0.6 | 0.5 |" in rollback["table"]


def test_apply_refuses_wrong_fingerprint(api: respx.MockRouter, tools: AdsTools) -> None:
    api.post(f"{NA}/sp/keywords/list").mock(return_value=keyword_list(0.5))
    update = api.put(f"{NA}/sp/keywords")
    planned = tools.plan_update("US", "keywords", [{"keywordId": "1", "bid": 0.6}])
    with pytest.raises(PermissionError, match="Fingerprint"):
        tools.apply_plan(planned["plan_id"], "0" * 64)
    assert not update.called


def test_a_plan_cannot_be_applied_twice(api: respx.MockRouter, tools: AdsTools) -> None:
    api.post(f"{NA}/sp/keywords/list").mock(return_value=keyword_list(0.5))
    api.put(f"{NA}/sp/keywords").mock(
        return_value=Response(207, json={"keywords": {"success": [{"index": 0, "keywordId": "1"}]}})
    )
    planned = tools.plan_update("US", "keywords", [{"keywordId": "1", "bid": 0.6}])
    tools.apply_plan(planned["plan_id"], planned["fingerprint"], check_drift=False)
    with pytest.raises(PermissionError, match="already applied"):
        tools.apply_plan(planned["plan_id"], planned["fingerprint"], check_drift=False)


def test_drift_is_reported_not_applied(api: respx.MockRouter, tools: AdsTools) -> None:
    api.post(f"{NA}/sp/keywords/list").mock(side_effect=[keyword_list(0.5), keyword_list(0.55)])
    update = api.put(f"{NA}/sp/keywords")
    planned = tools.plan_update("US", "keywords", [{"keywordId": "1", "bid": 0.6}])
    outcome = tools.apply_plan(planned["plan_id"], planned["fingerprint"])
    assert outcome["applied"] is False
    assert not update.called


def test_raw_calls_that_can_write_are_refused(tools: AdsTools) -> None:
    with pytest.raises(PermissionError):
        tools.call_operation("US", "sponsored-products:UpdateSponsoredProductsKeywords", {})


@pytest.mark.parametrize(
    "operation",
    [
        # read_only classifies this one as a read.
        "mcp-billing:list_invoices",
        # read_only classifies this one as a write, so a check made after the read_only
        # check would answer "use plan_update", which cannot rename an account.
        "mcp-account-management:update_account_name",
    ],
)
def test_call_operation_refuses_mcp_tools_for_what_they_are(
    api: respx.MockRouter,
    tools: AdsTools,
    operation: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The server must refuse on its own, not rely on ProfileClient.call doing it later:
    resolving the market's profile first can already mean a token and a profiles call."""

    def must_not_be_reached(*args: object, **kwargs: object) -> None:
        raise AssertionError("call_operation reached ProfileClient.call")

    monkeypatch.setattr(ProfileClient, "call", must_not_be_reached)
    with pytest.raises(UnsupportedOperationError, match="hosted Ads MCP server"):
        tools.call_operation("US", operation, {})
    assert not api.calls


def test_search_operations_offers_agents_no_mcp_tools_and_explains_corrections(
    tools: AdsTools,
) -> None:
    rows = {r["id"]: r for r in tools.search_operations("invoices")}
    assert not any(i.startswith("mcp-") or "unsupported" in r for i, r in rows.items())
    assert "406" in rows["advertising-billing:getAdvertiserInvoices"]["note"]


def test_search_operations_scoped_to_mcp_tools_says_why_they_cannot_be_called(
    tools: AdsTools,
) -> None:
    """An agent that names an mcp- API should learn the tools are refused before it spends
    a call finding out."""
    rows = tools.search_operations("invoices", api="mcp-billing")
    assert rows
    assert all("hosted Ads MCP server" in r["unsupported"] for r in rows)


def test_creating_targets_through_call_operation_is_refused(
    api: respx.MockRouter, tools: AdsTools
) -> None:
    # POST /sp/targets once counted as read-only because "get" is a substring of
    # "targets", so an agent could create targets with no plan and no approval.
    create = api.post(f"{NA}/sp/targets")
    with pytest.raises(PermissionError, match="can change data"):
        tools.call_operation(
            "US",
            "sponsored-products:CreateSponsoredProductsTargetingClauses",
            {"targetingClauses": [{"campaignId": "1", "adGroupId": "2", "bid": 0.5}]},
        )
    assert not create.called


def test_a_path_parameter_cannot_turn_a_read_into_a_write(
    api: respx.MockRouter, ads: AmazonAds, tmp_path: Path
) -> None:
    # The stores insights operation is a read-only POST, so call_operation allows it.
    # "../sp/targets?x=" used to resolve its path to POST /sp/targets: a write made with
    # no plan, and retried after a 503 because the operation looked read-only. The call
    # must be refused before anything is sent, including the profile lookup a market's
    # first call makes, which is why no profile is cached here.
    api.get(f"{NA}/v2/profiles").mock(
        return_value=Response(200, json=[{"profileId": US_PROFILE, "countryCode": "US"}])
    )
    api.get(path="/v2/profiles").mock(return_value=Response(200, json=[]))
    api.post(f"{NA}/sp/targets").mock(
        side_effect=[Response(503), Response(207, json={"targets": {"success": []}})]
    )
    with pytest.raises(ValueError, match="brandEntityId"):
        AdsTools(ads, plan_dir=tmp_path).call_operation(
            "US",
            "stores:getInsightsForStoreAPI",
            {"startDate": "2026-01-01", "endDate": "2026-01-31"},
            path_params={"brandEntityId": "../sp/targets?x="},
        )
    assert not api.calls, [str(call.request.url) for call in api.calls]


def test_plan_ids_cannot_escape_the_plan_directory(tools: AdsTools) -> None:
    with pytest.raises(ValueError, match="alphanumeric"):
        tools.get_plan("../../etc/passwd")


def test_read_only_server_registers_no_write_tools(tools: AdsTools) -> None:
    names = {t.name for t in asyncio.run(build_server(tools, read_only=True).list_tools())}
    assert "list_entities" in names
    assert not names & {"plan_update", "plan_create", "plan_archive", "apply_plan"}
    full = {t.name for t in asyncio.run(build_server(tools, read_only=False).list_tools())}
    assert {"plan_update", "apply_plan"} <= full


def test_aggregate_sums_metrics_and_derives_rates() -> None:
    rows = [
        {
            "date": "d1",
            "campaignName": "A",
            "campaignId": 1,
            "impressions": 100,
            "clicks": 4,
            "cost": 2.0,
            "sales14d": 10.0,
            "keywordBid": 0.5,
        },
        {
            "date": "d2",
            "campaignName": "A",
            "campaignId": 1,
            "impressions": 100,
            "clicks": 0,
            "cost": 0.0,
            "sales14d": 0.0,
            "keywordBid": 0.5,
        },
    ]
    [total] = aggregate(rows, ["campaignName"])
    assert total["impressions"] == 200
    assert total["cpc"] == 0.5
    assert total["acos14d"] == 0.2
    assert "keywordBid" not in total
    assert "campaignId" not in total


def test_unexpected_error_keeps_its_reason():
    """An exception type outside EXPECTED_ERRORS used to propagate raw, and the MCP client
    showed only "Error executing tool <name>". A half-updated server process raised
    ImportError that way and the cause was invisible for a day."""
    from amazon_ads.mcp.server import _surface_errors

    class ToolError(Exception):
        pass

    def boom():
        raise ImportError("cannot import name '_coerce_object_fields'")

    with pytest.raises(ToolError) as caught:
        _surface_errors(boom, ToolError)()

    message = str(caught.value)
    assert "ImportError" in message
    assert "_coerce_object_fields" in message
    assert "boom" in message, "the traceback frames should reach the caller too"
