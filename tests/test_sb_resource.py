from __future__ import annotations

import json
from urllib.parse import parse_qs, urlparse

import pytest
import respx
from httpx import Request, Response

from amazon_ads import AmazonAds, ProfileClient
from amazon_ads.mcp.server import AdsTools
from amazon_ads.models import Profile
from tests.conftest import NA, US_PROFILE

# Invented ids and keywords. Nothing here comes from a real account.
KEYWORD = {
    "keywordId": 101,
    "adGroupId": 202,
    "campaignId": 303,
    "keywordText": "mystery novels",
    "matchType": "phrase",
    "state": "enabled",
    "bid": 0.75,
}


def query(request: Request) -> dict[str, str]:
    return {k: v[0] for k, v in parse_qs(urlparse(str(request.url)).query).items()}


@pytest.fixture
def tools(ads: AmazonAds, tmp_path) -> AdsTools:  # type: ignore[no-untyped-def]
    ads._profiles = [Profile(profile_id=US_PROFILE, country_code="US", currency_code="USD")]
    return AdsTools(ads, plan_dir=tmp_path)


def test_keyword_list_pages_by_start_index_until_a_short_page(
    api: respx.MockRouter, us: ProfileClient
) -> None:
    route = api.get(f"{NA}/sb/keywords").mock(
        side_effect=[
            Response(200, json=[{**KEYWORD, "keywordId": 1}, {**KEYWORD, "keywordId": 2}]),
            Response(200, json=[{**KEYWORD, "keywordId": 3}]),
        ]
    )
    found = us.sb.keywords.list(campaign_ids=[303], page_size=2)
    assert [k.keyword_id for k in found] == [1, 2, 3]
    first, second = (query(c.request) for c in route.calls)
    assert first["campaignIdFilter"] == "303"
    assert first["stateFilter"] == "enabled,paused"
    assert (first["startIndex"], second["startIndex"]) == ("0", "2")


def test_negative_keywords_ask_for_one_state_per_request(
    api: respx.MockRouter, us: ProfileClient
) -> None:
    # The negative-keyword list takes a single state, so "every state" is two requests.
    route = api.get(f"{NA}/sb/negativeKeywords").mock(return_value=Response(200, json=[]))
    us.sb.negative_keywords.list(states=None)
    assert sorted(query(c.request)["stateFilter"] for c in route.calls) == ["archived", "enabled"]


def test_update_adds_the_parent_ids_amazon_requires(
    api: respx.MockRouter, us: ProfileClient
) -> None:
    api.get(f"{NA}/sb/keywords").mock(return_value=Response(200, json=[KEYWORD]))
    put = api.put(f"{NA}/sb/keywords").mock(
        return_value=Response(207, json=[{"code": "SUCCESS", "keywordId": 101}])
    )
    result = us.sb.keywords.update([{"keywordId": "101", "bid": 0.95, "state": "PAUSED"}])
    assert result.ok
    assert json.loads(put.calls.last.request.content) == [
        {"keywordId": 101, "adGroupId": 202, "campaignId": 303, "bid": 0.95, "state": "paused"}
    ]


def test_update_reports_rejected_items_without_raising(
    api: respx.MockRouter, us: ProfileClient
) -> None:
    api.get(f"{NA}/sb/keywords").mock(return_value=Response(200, json=[KEYWORD]))
    api.put(f"{NA}/sb/keywords").mock(
        return_value=Response(
            207, json=[{"code": "INVALID_ARGUMENT", "description": "bid below minimum"}]
        )
    )
    result = us.sb.keywords.update([{"keywordId": 101, "bid": 0.01}])
    assert not result.ok
    assert result.errors[0].message == "bid below minimum"


def test_update_over_100_keywords_is_sent_in_chunks(
    api: respx.MockRouter, us: ProfileClient
) -> None:
    def respond(request: Request) -> Response:
        sent = json.loads(request.content)
        return Response(207, json=[{"code": "SUCCESS", "keywordId": k["keywordId"]} for k in sent])

    put = api.put(f"{NA}/sb/keywords").mock(side_effect=respond)
    items = [{"keywordId": i, "adGroupId": 1, "campaignId": 2, "bid": 1.0} for i in range(150)]
    result = us.sb.keywords.update(items)
    assert [len(json.loads(c.request.content)) for c in put.calls] == [100, 50]
    assert result.successes[120].id == "120"


def test_plan_apply_and_rollback_restore_the_old_bid(
    api: respx.MockRouter, tools: AdsTools
) -> None:
    api.get(f"{NA}/sb/keywords").mock(
        side_effect=[
            Response(200, json=[KEYWORD]),  # plan build
            Response(200, json=[KEYWORD]),  # drift check
            Response(200, json=[KEYWORD]),  # parent ids for the update
        ]
    )
    put = api.put(f"{NA}/sb/keywords").mock(
        return_value=Response(207, json=[{"code": "SUCCESS", "keywordId": 101}])
    )
    planned = tools.plan_update("US", "sb_keywords", [{"keywordId": "101", "bid": 0.95}])
    assert (
        "| update | sb.keywords | 101 | mystery novels [phrase] | bid | 0.75 | 0.95 |"
        in (planned["table"])
    )
    assert not put.called

    applied = tools.apply_plan(planned["plan_id"], planned["fingerprint"])
    assert applied["ok"] is True
    rollback = tools.get_plan(applied["rollback_plan_id"])
    assert "| bid | 0.95 | 0.75 |" in rollback["table"]


def test_created_negatives_roll_back_by_archiving_each_one(
    api: respx.MockRouter, tools: AdsTools
) -> None:
    post = api.post(f"{NA}/sb/negativeKeywords").mock(
        return_value=Response(
            207,
            json=[
                {"code": "SUCCESS", "keywordId": 901},
                {"code": "SUCCESS", "keywordId": 902},
            ],
        )
    )
    items = [
        {"campaignId": "303", "adGroupId": "202", "keywordText": t, "matchType": "negativeExact"}
        for t in ("free pdf", "audiobook")
    ]
    planned = tools.plan_create("US", "sb_negative_keywords", items)
    applied = tools.apply_plan(planned["plan_id"], planned["fingerprint"])
    assert applied["created_ids"] == {"create sb.negative_keywords": ["901", "902"]}
    sent = json.loads(post.calls.last.request.content)
    assert sent[0] == {
        "campaignId": 303,
        "adGroupId": 202,
        "keywordText": "free pdf",
        "matchType": "negativeExact",
    }

    deletes = [
        api.delete(f"{NA}/sb/negativeKeywords/{i}").mock(
            return_value=Response(200, json={"code": "SUCCESS", "keywordId": i})
        )
        for i in (901, 902)
    ]
    rollback = tools.get_plan(applied["rollback_plan_id"])
    undone = tools.apply_plan(rollback["plan_id"], rollback["fingerprint"], check_drift=False)
    assert undone["ok"] is True
    assert all(d.called for d in deletes)


def test_archiving_a_missing_keyword_is_an_item_error(
    api: respx.MockRouter, us: ProfileClient
) -> None:
    api.delete(f"{NA}/sb/keywords/5").mock(return_value=Response(404, json={"code": "NOT_FOUND"}))
    result = us.sb.keywords.delete([5])
    assert not result.ok
    assert result.errors[0].code == "NOT_FOUND"


def test_campaign_placement_premium_goes_through_the_v4_batch_contract(
    api: respx.MockRouter, us: ProfileClient
) -> None:
    put = api.put(f"{NA}/sb/v4/campaigns").mock(
        return_value=Response(
            207, json={"campaigns": {"success": [{"index": 0, "campaignId": "303"}], "error": []}}
        )
    )
    bidding = {
        "bidOptimization": False,
        "bidAdjustmentsByPlacement": [{"placement": "TOP_OF_SEARCH", "percentage": 60}],
    }
    result = us.sb.campaigns.update([{"campaignId": "303", "bidding": bidding, "goal": "x"}])
    assert result.ok
    # goal is not updatable and is dropped rather than sent and rejected.
    assert json.loads(put.calls.last.request.content) == {
        "campaigns": [{"campaignId": "303", "bidding": bidding}]
    }
    assert put.calls.last.request.headers["Content-Type"] == (
        "application/vnd.sbcampaignresource.v4+json"
    )


def test_campaign_updates_respect_the_v4_limit_of_ten(
    api: respx.MockRouter, us: ProfileClient
) -> None:
    def respond(request: Request) -> Response:
        sent = json.loads(request.content)["campaigns"]
        success = [{"index": i, "campaignId": c["campaignId"]} for i, c in enumerate(sent)]
        return Response(207, json={"campaigns": {"success": success}})

    put = api.put(f"{NA}/sb/v4/campaigns").mock(side_effect=respond)
    us.sb.campaigns.update([{"campaignId": str(i), "budget": 10.0} for i in range(12)])
    assert [len(json.loads(c.request.content)["campaigns"]) for c in put.calls] == [10, 2]


def test_unknown_entity_names_list_the_sponsored_brands_ones(tools: AdsTools) -> None:
    # sb_billboards is not an entity. sb_ads and sb_ad_groups are, so they cannot stand in
    # for an unknown name here.
    with pytest.raises(ValueError, match="sb_negative_keywords"):
        tools.plan_update("US", "sb_billboards", [])


# --- ad groups and ads (v4) ---------------------------------------------------------------

# Invented ids, ASINs and copy. Nothing here comes from a real account.
AD_GROUP = {"adGroupId": "404", "campaignId": "303", "name": "mystery ad group", "state": "ENABLED"}
AD_CREATIVE = {
    "brandName": "Invented Books",
    "headline": "Three mysteries to start with",
    "asins": ["B000000001", "B000000002", "B000000003"],
}


def test_ad_group_create_posts_to_the_shared_v4_path(
    api: respx.MockRouter, us: ProfileClient
) -> None:
    post = api.post(f"{NA}/sb/v4/adGroups").mock(
        return_value=Response(
            207, json={"adGroups": {"success": [{"index": 0, "adGroupId": "404"}], "error": []}}
        )
    )
    result = us.sb.ad_groups.create([AD_GROUP])
    assert result.ok
    assert result.successes[0].id == "404"
    assert json.loads(post.calls.last.request.content) == {"adGroups": [AD_GROUP]}
    assert post.calls.last.request.headers["Content-Type"] == (
        "application/vnd.sbadgroupresource.v4+json"
    )


def test_ad_create_posts_to_the_endpoint_for_its_format_and_drops_ad_format(
    api: respx.MockRouter, us: ProfileClient
) -> None:
    post = api.post(f"{NA}/sb/v4/ads/productCollection").mock(
        return_value=Response(
            207, json={"ads": {"success": [{"index": 0, "adId": "505"}], "error": []}}
        )
    )
    result = us.sb.ads.create(
        [
            {
                "adGroupId": "404",
                "name": "mystery ad",
                "state": "ENABLED",
                "adFormat": "productCollection",
                "creative": AD_CREATIVE,
                "landingPage": {"pageType": "DETAIL_PAGE", "asins": ["B000000001"]},
            }
        ]
    )
    assert result.ok
    assert result.successes[0].id == "505"
    sent = json.loads(post.calls.last.request.content)["ads"][0]
    # adFormat chooses the endpoint and is not a field Amazon accepts in the body.
    assert "adFormat" not in sent
    assert sent["creative"] == AD_CREATIVE
    assert post.calls.last.request.headers["Content-Type"] == (
        "application/vnd.sbadresource.v4+json"
    )


def test_ad_create_defaults_to_the_collections_format(
    api: respx.MockRouter, us: ProfileClient
) -> None:
    post = api.post(f"{NA}/sb/v4/ads/productCollection").mock(
        return_value=Response(207, json={"ads": {"success": [{"index": 0, "adId": "505"}]}})
    )
    us.sb.ads.create([{"adGroupId": "404", "name": "mystery ad", "state": "ENABLED"}])
    assert post.called


def test_ad_create_refuses_to_mix_formats_because_207_indexes_are_per_request(
    us: ProfileClient,
) -> None:
    with pytest.raises(ValueError, match="one call per format"):
        us.sb.ads.create(
            [
                {"adGroupId": "404", "adFormat": "productCollection"},
                {"adGroupId": "404", "adFormat": "storeSpotlight"},
            ]
        )


def test_ad_create_rejects_a_format_with_no_endpoint(us: ProfileClient) -> None:
    with pytest.raises(ValueError, match="unknown Sponsored Brands ad format"):
        us.sb.ads.create([{"adGroupId": "404", "adFormat": "billboard"}])


def test_ad_update_and_list_use_the_shared_v4_contract(
    api: respx.MockRouter, us: ProfileClient
) -> None:
    listed = api.post(f"{NA}/sb/v4/ads/list").mock(
        return_value=Response(200, json={"ads": [{"adId": "505", "adGroupId": "404"}]})
    )
    assert [a.ad_id for a in us.sb.ads.list()] == ["505"]
    assert listed.called

    put = api.put(f"{NA}/sb/v4/ads").mock(
        return_value=Response(207, json={"ads": {"success": [{"index": 0, "adId": "505"}]}})
    )
    result = us.sb.ads.update([{"adId": "505", "state": "PAUSED", "adGroupId": "404"}])
    assert result.ok
    # adGroupId is not updatable on an ad and is dropped rather than sent and rejected.
    assert json.loads(put.calls.last.request.content) == {
        "ads": [{"adId": "505", "state": "PAUSED"}]
    }


def test_the_mcp_server_lists_sponsored_brands_ad_groups_and_ads(
    api: respx.MockRouter, tools: AdsTools
) -> None:
    api.post(f"{NA}/sb/v4/adGroups/list").mock(
        return_value=Response(200, json={"adGroups": [AD_GROUP]})
    )
    assert tools.list_entities("US", "sb_ad_groups")["sb_ad_groups"][0]["adGroupId"] == "404"

    api.post(f"{NA}/sb/v4/ads/list").mock(
        return_value=Response(200, json={"ads": [{"adId": "505", "adGroupId": "404"}]})
    )
    assert tools.list_entities("US", "sb_ads")["sb_ads"][0]["adId"] == "505"
