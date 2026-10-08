from __future__ import annotations

import json

import pytest
import respx
from httpx import Request, Response

from amazon_ads import Keyword, ProfileClient
from amazon_ads.models import Budget
from tests.conftest import NA


def test_list_follows_next_token(api: respx.MockRouter, us: ProfileClient) -> None:
    route = api.post(f"{NA}/sp/keywords/list").mock(
        side_effect=[
            Response(200, json={"keywords": [{"keywordId": "1"}], "nextToken": "t2"}),
            Response(200, json={"keywords": [{"keywordId": "2"}]}),
        ]
    )
    assert [k.keyword_id for k in us.sp.keywords.list()] == ["1", "2"]
    assert json.loads(route.calls[1].request.content)["nextToken"] == "t2"


def test_targets_use_amazons_collection_key(api: respx.MockRouter, us: ProfileClient) -> None:
    api.post(f"{NA}/sp/targets/list").mock(
        return_value=Response(
            200,
            json={
                "targetingClauses": [
                    {"targetId": "7", "expression": [{"type": "ASIN_SAME_AS", "value": "B0X"}]}
                ]
            },
        )
    )
    [target] = us.sp.targets.list()
    assert target.asin == "B0X"


def test_long_id_filters_are_split_at_amazons_limit(
    api: respx.MockRouter, us: ProfileClient
) -> None:
    route = api.post(f"{NA}/sp/keywords/list").mock(
        return_value=Response(200, json={"keywords": []})
    )
    us.sp.keywords.list(ids=[str(i) for i in range(2500)])
    sizes = [len(json.loads(c.request.content)["keywordIdFilter"]["include"]) for c in route.calls]
    assert sizes == [1000, 1000, 500]


def test_update_drops_fields_amazon_treats_as_read_only(
    api: respx.MockRouter, us: ProfileClient
) -> None:
    route = api.put(f"{NA}/sp/keywords").mock(
        return_value=Response(207, json={"keywords": {"success": [{"index": 0, "keywordId": "1"}]}})
    )
    us.sp.keywords.update(
        [Keyword(keyword_id="1", keyword_text="mystery novels", match_type="EXACT", bid=0.4)]
    )
    sent = json.loads(route.calls.last.request.content)
    assert sent == {"keywords": [{"keywordId": "1", "bid": 0.4}]}


def test_create_over_1000_items_is_chunked_and_indexes_stay_aligned(
    api: respx.MockRouter, us: ProfileClient
) -> None:
    def respond(request: Request) -> Response:
        sent = json.loads(request.content)["keywords"]
        success = [
            {"index": i, "keywordId": f"id-{item['keywordText']}"} for i, item in enumerate(sent)
        ]
        return Response(207, json={"keywords": {"success": success, "error": []}})

    route = api.post(f"{NA}/sp/keywords").mock(side_effect=respond)
    items = [{"campaignId": "1", "adGroupId": "2", "keywordText": f"k{i}"} for i in range(2500)]
    result = us.sp.keywords.create(items)
    assert route.call_count == 3
    assert len(result.successes) == 2500
    assert result.successes[1700].index == 1700
    assert result.successes[1700].id == "id-k1700"
    assert result.successes[1700].item == items[1700]


def test_negative_keyword_results_read_the_negative_id_field(
    api: respx.MockRouter, us: ProfileClient
) -> None:
    api.post(f"{NA}/sp/negativeKeywords").mock(
        return_value=Response(
            207,
            json={"negativeKeywords": {"success": [{"index": 0, "negativeKeywordId": "55"}]}},
        )
    )
    result = us.sp.negative_keywords.create(
        [
            {
                "campaignId": "1",
                "adGroupId": "2",
                "keywordText": "free",
                "matchType": "NEGATIVE_EXACT",
            }
        ]
    )
    assert result.ids == ["55"]


def test_snake_case_dicts_are_sent_in_amazons_spelling(
    api: respx.MockRouter, us: ProfileClient
) -> None:
    route = api.post(f"{NA}/sp/keywords").mock(
        return_value=Response(207, json={"keywords": {"success": []}})
    )
    us.sp.keywords.create([{"campaign_id": "1", "ad_group_id": "2", "keyword_text": "x"}])
    assert json.loads(route.calls.last.request.content)["keywords"][0] == {
        "campaignId": "1",
        "adGroupId": "2",
        "keywordText": "x",
    }


def test_plan_update_records_only_real_changes(api: respx.MockRouter, us: ProfileClient) -> None:
    api.post(f"{NA}/sp/keywords/list").mock(
        return_value=Response(
            200,
            json={
                "keywords": [
                    {"keywordId": "1", "keywordText": "a", "matchType": "EXACT", "bid": 0.5},
                    {"keywordId": "2", "keywordText": "b", "matchType": "EXACT", "bid": 0.3},
                ]
            },
        )
    )
    plan = us.sp.keywords.plan_update(
        [
            {"keywordId": "1", "bid": 0.6},
            {"keywordId": "2", "bid": 0.3},
            {"keywordId": "9", "bid": 1},
        ]
    )
    assert len(plan) == 1
    [change] = plan.changes
    assert (change.id, change.before, change.after) == ("1", {"bid": 0.5}, {"bid": 0.6})
    assert change.label == "a [EXACT]"
    assert plan.missing == ["9"]
    assert plan.profile_id == us.profile_id


def throttled_or_ok(throttle_first: set[str]):  # type: ignore[no-untyped-def]
    """Answer a keyword write, throttling each id in ``throttle_first`` on its first send."""
    seen: set[str] = set()

    def respond(request: Request) -> Response:
        sent = json.loads(request.content)["keywords"]
        success, error = [], []
        for i, item in enumerate(sent):
            key = item.get("keywordId") or item.get("keywordText")
            if key in throttle_first and key not in seen:
                seen.add(key)
                error.append(
                    {
                        "index": i,
                        "errors": [
                            {
                                "errorType": "throttledError",
                                "errorValue": {"throttledError": {"reason": "THROTTLED"}},
                            }
                        ],
                    }
                )
            else:
                success.append({"index": i, "keywordId": f"id-{key}"})
        return Response(207, json={"keywords": {"success": success, "error": error}})

    return respond


def test_items_amazon_throttled_inside_a_batch_are_resent_alone(
    api: respx.MockRouter, us: ProfileClient, sleeps
) -> None:  # type: ignore[no-untyped-def]
    route = api.put(f"{NA}/sp/keywords").mock(side_effect=throttled_or_ok({"b"}))
    result = us.sp.keywords.update(
        [{"keywordId": "a", "bid": 1}, {"keywordId": "b", "bid": 1}, {"keywordId": "c", "bid": 1}]
    )
    assert result.ok
    assert [s.index for s in result.successes] == [0, 1, 2]
    assert result.successes[1].item == {"keywordId": "b", "bid": 1}
    assert json.loads(route.calls[1].request.content) == {
        "keywords": [{"keywordId": "b", "bid": 1}]
    }
    assert len(sleeps) == 1


def test_create_is_not_resent_after_an_internal_error(
    api: respx.MockRouter, us: ProfileClient
) -> None:
    # The keyword may have been created before Amazon failed; a resend could duplicate it.
    route = api.post(f"{NA}/sp/keywords").mock(
        return_value=Response(
            207,
            json={
                "keywords": {
                    "error": [
                        {
                            "index": 0,
                            "errors": [
                                {
                                    "errorType": "internalServerError",
                                    "errorValue": {
                                        "internalServerError": {"reason": "INTERNAL_ERROR"}
                                    },
                                }
                            ],
                        }
                    ]
                }
            },
        )
    )
    result = us.sp.keywords.create([{"campaignId": "1", "adGroupId": "2", "keywordText": "x"}])
    assert route.call_count == 1
    assert "check whether it exists" in result.errors[0].hint


def test_sp_campaign_budget_given_as_a_number_is_sent_as_the_object_amazon_requires(
    api: respx.MockRouter, us: ProfileClient
) -> None:
    # Amazon rejects {"budget": 20} on PUT /sp/campaigns with a 400 that names no field.
    route = api.put(f"{NA}/sp/campaigns").mock(
        return_value=Response(
            207, json={"campaigns": {"success": [{"index": 0, "campaignId": "9"}]}}
        )
    )
    us.sp.campaigns.update([{"campaignId": "9", "budget": 20}])
    sent = json.loads(route.calls.last.request.content)
    assert sent == {
        "campaigns": [{"campaignId": "9", "budget": {"budget": 20.0, "budgetType": "DAILY"}}]
    }


def test_sp_campaign_create_sends_a_number_budget_as_an_object_too(
    api: respx.MockRouter, us: ProfileClient
) -> None:
    route = api.post(f"{NA}/sp/campaigns").mock(
        return_value=Response(
            207, json={"campaigns": {"success": [{"index": 0, "campaignId": "9"}]}}
        )
    )
    us.sp.campaigns.create([{"name": "Mysteries", "state": "PAUSED", "budget": 15}])
    [campaign] = json.loads(route.calls.last.request.content)["campaigns"]
    assert campaign["budget"] == {"budget": 15.0, "budgetType": "DAILY"}


def test_sp_campaign_budget_already_an_object_is_passed_through(
    api: respx.MockRouter, us: ProfileClient
) -> None:
    route = api.put(f"{NA}/sp/campaigns").mock(
        return_value=Response(
            207, json={"campaigns": {"success": [{"index": 0, "campaignId": "9"}]}}
        )
    )
    us.sp.campaigns.update(
        [{"campaignId": "9", "budget": {"budget": 20, "budgetType": "LIFETIME"}}]
    )
    [campaign] = json.loads(route.calls.last.request.content)["campaigns"]
    assert campaign["budget"] == {"budget": 20.0, "budgetType": "LIFETIME"}


def test_sp_campaign_budget_of_an_impossible_type_is_refused_before_any_request(
    api: respx.MockRouter, us: ProfileClient
) -> None:
    route = api.put(f"{NA}/sp/campaigns")
    with pytest.raises(ValueError, match=r"sp\.campaigns budget takes a number"):
        us.sp.campaigns.update([{"campaignId": "9", "budget": ["20"]}])
    assert route.call_count == 0


def test_sb_campaign_budget_stays_a_flat_number(api: respx.MockRouter, us: ProfileClient) -> None:
    # A Sponsored Brands campaign holds its budget as a number beside a sibling
    # budgetType, so the widening that Sponsored Products needs would break it.
    route = api.put(f"{NA}/sb/v4/campaigns").mock(
        return_value=Response(
            207, json={"campaigns": {"success": [{"index": 0, "campaignId": "303"}]}}
        )
    )
    us.sb.campaigns.update([{"campaignId": "303", "budget": 20}])
    sent = json.loads(route.calls.last.request.content)
    assert sent == {"campaigns": [{"campaignId": "303", "budget": 20}]}


def test_sp_campaign_budget_accepts_the_snake_case_spelling_and_the_model(
    api: respx.MockRouter, us: ProfileClient
) -> None:
    # Dict items may be written either way, so a budget_type key must not end up beside
    # an inherited budgetType, where it would lose and still be sent as an extra field.
    api.post(f"{NA}/sp/campaigns/list").mock(
        return_value=Response(
            200,
            json={
                "campaigns": [
                    {
                        "campaignId": "9",
                        "name": "Mysteries",
                        "budget": {"budget": 500.0, "budgetType": "LIFETIME"},
                    }
                ]
            },
        )
    )
    plan = us.sp.campaigns.plan_update(
        [{"campaignId": "9", "budget": {"budget": 20, "budget_type": "DAILY"}}]
    )
    [change] = plan.changes
    assert change.after == {"budget": {"budget": 20.0, "budgetType": "DAILY"}}

    route = api.put(f"{NA}/sp/campaigns").mock(
        return_value=Response(
            207, json={"campaigns": {"success": [{"index": 0, "campaignId": "9"}]}}
        )
    )
    us.sp.campaigns.update([{"campaignId": "9", "budget": Budget(budget=5)}])
    [campaign] = json.loads(route.calls.last.request.content)["campaigns"]
    assert campaign["budget"] == {"budget": 5.0, "budgetType": "DAILY"}


def test_sp_campaign_budget_ignores_read_only_keys_a_read_returned(
    api: respx.MockRouter, us: ProfileClient
) -> None:
    # Echoing a field Amazon only reports back would be rejected, so only the keys the
    # Budget model names are inherited from the campaign's current budget.
    api.post(f"{NA}/sp/campaigns/list").mock(
        return_value=Response(
            200,
            json={
                "campaigns": [
                    {
                        "campaignId": "9",
                        "name": "Mysteries",
                        "budget": {
                            "budget": 10.0,
                            "budgetType": "DAILY",
                            "effectiveBudget": 9.5,
                        },
                    }
                ]
            },
        )
    )
    [change] = us.sp.campaigns.plan_update([{"campaignId": "9", "budget": 20}]).changes
    assert change.after == {"budget": {"budget": 20.0, "budgetType": "DAILY"}}


def test_specs_stay_hashable(us: ProfileClient) -> None:
    # Specs are plain frozen values; a caller keying a dict or set by spec must not break
    # because one of their fields is a mapping.
    from amazon_ads.sb import ALL_SPECS as SB_SPECS
    from amazon_ads.sp import ALL_SPECS as SP_SPECS

    assert len({*SP_SPECS, *SB_SPECS}) == len(SP_SPECS) + len(SB_SPECS)
