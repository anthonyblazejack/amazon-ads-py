from __future__ import annotations

import asyncio
import json

import pytest
import respx
from httpx import Response

from amazon_ads import AmazonAds
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
