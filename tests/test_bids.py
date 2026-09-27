from __future__ import annotations

import json

import respx
from httpx import Request, Response

from amazon_ads import Keyword, ProfileClient, Target
from amazon_ads.bids import Expression, parse_recommendations
from tests.conftest import NA

URL = f"{NA}/sp/targets/bid/recommendations"


def echo_recommendations(request: Request) -> Response:
    body = json.loads(request.content)
    entries = []
    for i, expr in enumerate(body["targetingExpressions"]):
        base = 0.5 + i / 100
        entries.append(
            {
                "targetingExpression": expr,
                "bidValues": [
                    {"suggestedBid": base},
                    {"suggestedBid": base + 0.2},
                    {"suggestedBid": base + 0.4},
                ],
            }
        )
    return Response(
        200,
        json={
            "bidRecommendations": [
                {
                    "theme": "CONVERSION_OPPORTUNITIES",
                    "bidRecommendationsForTargetingExpressions": entries,
                }
            ]
        },
    )


def test_keyword_and_asin_expressions_never_share_a_request(
    api: respx.MockRouter, us: ProfileClient
) -> None:
    route = api.post(URL).mock(side_effect=echo_recommendations)
    us.bids.for_ad_group(
        "c",
        "g",
        [Expression("KEYWORD_EXACT_MATCH", "mystery novels"), Expression("PAT_ASIN", "B0TEST")],
    )
    families = [
        {e["type"] for e in json.loads(c.request.content)["targetingExpressions"]}
        for c in route.calls
    ]
    assert sorted(map(sorted, families)) == [["KEYWORD_EXACT_MATCH"], ["PAT_ASIN"]]
    assert route.calls.last.request.headers["Content-Type"] == (
        "application/vnd.spthemebasedbidrecommendation.v4+json"
    )


def test_product_targets_are_asked_about_as_pat_asin(
    api: respx.MockRouter, us: ProfileClient
) -> None:
    route = api.post(URL).mock(side_effect=echo_recommendations)
    same = Target(
        target_id="1",
        campaign_id="c",
        ad_group_id="g",
        expression=[{"type": "ASIN_SAME_AS", "value": "B0SAME"}],
    )
    expanded = Target(
        target_id="2",
        campaign_id="c",
        ad_group_id="g",
        expression=[{"type": "ASIN_EXPANDED_FROM", "value": "B0EXP"}],
    )
    result = us.bids.for_targets([same, expanded])
    sent = json.loads(route.calls.last.request.content)["targetingExpressions"]
    assert sent == [{"type": "PAT_ASIN", "value": "B0SAME"}]
    assert [s.value for s in result.suggestions] == ["B0SAME"]
    assert result.suggestions[0].median == 0.7
    assert result.unsupported == [expanded]


def test_more_than_100_expressions_are_chunked(api: respx.MockRouter, us: ProfileClient) -> None:
    route = api.post(URL).mock(side_effect=echo_recommendations)
    exprs = [Expression("PAT_ASIN", f"B0{i:08d}") for i in range(150)]
    found = us.bids.for_ad_group("c", "g", exprs)
    assert route.call_count == 2
    assert [s.value for s in found] == [e.value for e in exprs]


def test_expression_amazon_did_not_price_comes_back_empty_not_missing(
    api: respx.MockRouter, us: ProfileClient
) -> None:
    api.post(URL).mock(return_value=Response(200, json={"bidRecommendations": []}))
    [s] = us.bids.for_ad_group("c", "g", [Expression("PAT_ASIN", "B0AU")])
    assert (s.value, s.low, s.median, s.high, s.priced) == ("B0AU", None, None, None, False)


def test_keyword_suggestions_are_keyed_by_keyword_id(
    api: respx.MockRouter, us: ProfileClient
) -> None:
    api.post(URL).mock(side_effect=echo_recommendations)
    keywords = [
        Keyword(
            keyword_id="k1", campaign_id="c", ad_group_id="g", keyword_text="A", match_type="EXACT"
        ),
        Keyword(
            keyword_id="k2", campaign_id="c", ad_group_id="g", keyword_text="b", match_type="PHRASE"
        ),
    ]
    found = us.bids.for_keywords(keywords)
    assert set(found) == {"k1", "k2"}
    assert found["k2"].expression_type == "KEYWORD_PHRASE_MATCH"


def test_seasonal_themes_are_kept_apart() -> None:
    payload = {
        "bidRecommendations": [
            {
                "theme": "CONVERSION_OPPORTUNITIES",
                "bidRecommendationsForTargetingExpressions": [
                    {
                        "targetingExpression": {"type": "PAT_ASIN", "value": "B0"},
                        "bidValues": [
                            {"suggestedBid": 0.3},
                            {"suggestedBid": 0.4},
                            {"suggestedBid": 0.5},
                        ],
                    }
                ],
            },
            {
                "theme": "BFCM_HOLIDAY",
                "bidRecommendationsForTargetingExpressions": [
                    {
                        "targetingExpression": {"type": "PAT_ASIN", "value": "B0"},
                        "bidValues": [
                            {"suggestedBid": 0.6},
                            {"suggestedBid": 0.8},
                            {"suggestedBid": 1.0},
                        ],
                    }
                ],
            },
        ]
    }
    themes = parse_recommendations(payload)
    assert themes["CONVERSION_OPPORTUNITIES"][0].median == 0.4
    assert themes["BFCM_HOLIDAY"][0].median == 0.8
