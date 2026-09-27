from __future__ import annotations

import json

import pytest
import respx
from httpx import Response

from amazon_ads import (
    AmazonAds,
    ProfileClient,
    ServerError,
    ThrottledError,
    UnprocessableError,
)
from amazon_ads.transport import RetryPolicy, is_idempotent, parse_retry_after
from tests.conftest import NA, US_PROFILE, Sleeps


def test_429_waits_for_retry_after_then_succeeds(
    api: respx.MockRouter, us: ProfileClient, sleeps: Sleeps
) -> None:
    route = api.post(f"{NA}/sp/keywords/list").mock(
        side_effect=[
            Response(429, headers={"Retry-After": "7"}, json={"code": "429"}),
            Response(200, json={"keywords": []}),
        ]
    )
    assert us.sp.keywords.list() == []
    assert route.call_count == 2
    assert sleeps == [pytest.approx(7.0, abs=0.01)]


def test_429_is_retried_even_for_writes(
    api: respx.MockRouter, us: ProfileClient, sleeps: Sleeps
) -> None:
    # Amazon rejected the request without acting on it, so resending cannot duplicate.
    route = api.post(f"{NA}/sp/keywords").mock(
        side_effect=[
            Response(429, headers={"Retry-After": "1"}),
            Response(207, json={"keywords": {"success": [{"index": 0, "keywordId": "9"}]}}),
        ]
    )
    result = us.sp.keywords.create([{"campaignId": "1", "adGroupId": "2", "keywordText": "x"}])
    assert result.ids == ["9"]
    assert route.call_count == 2


def test_server_error_on_create_is_not_retried(api: respx.MockRouter, us: ProfileClient) -> None:
    # A create that failed with 5xx may still have gone through; retrying could make a
    # second copy of every keyword.
    route = api.post(f"{NA}/sp/keywords").mock(return_value=Response(503))
    with pytest.raises(ServerError):
        us.sp.keywords.create([{"campaignId": "1", "adGroupId": "2", "keywordText": "x"}])
    assert route.call_count == 1


def test_server_error_on_list_is_retried(api: respx.MockRouter, us: ProfileClient) -> None:
    route = api.post(f"{NA}/sp/keywords/list").mock(
        side_effect=[Response(502), Response(200, json={"keywords": [{"keywordId": "1"}]})]
    )
    assert [k.keyword_id for k in us.sp.keywords.list()] == ["1"]
    assert route.call_count == 2


def test_throttling_gives_up_after_max_attempts(api: respx.MockRouter, sleeps: Sleeps) -> None:
    from amazon_ads import Credentials
    from amazon_ads.models import Profile

    ads = AmazonAds(
        Credentials(client_id="c", client_secret="s", refresh_token="r"),
        retry=RetryPolicy(max_attempts=3),
        sleep=sleeps,
    )
    client = ProfileClient(ads, Profile(profile_id=US_PROFILE, country_code="US"))
    api.post(f"{NA}/sp/keywords/list").mock(
        return_value=Response(429, headers={"Retry-After": "2"})
    )
    with pytest.raises(ThrottledError) as info:
        client.sp.keywords.list()
    assert info.value.retry_after == 2.0
    assert len(sleeps) == 2


def test_401_refreshes_token_once(api: respx.MockRouter, us: ProfileClient) -> None:
    token = api.routes["token"]
    route = api.post(f"{NA}/sp/keywords/list").mock(
        side_effect=[Response(401), Response(200, json={"keywords": []})]
    )
    us.sp.keywords.list()
    assert route.call_count == 2
    assert token.call_count == 2


def test_error_body_becomes_readable_exception(api: respx.MockRouter, us: ProfileClient) -> None:
    api.post(f"{NA}/sp/targets/bid/recommendations").mock(
        return_value=Response(
            422,
            headers={"x-amz-request-id": "req-1"},
            json={
                "code": "422",
                "details": "All targeting expressions should have the same targeting type",
            },
        )
    )
    with pytest.raises(UnprocessableError) as info:
        us.request("POST", "/sp/targets/bid/recommendations", json={})
    assert "same targeting type" in str(info.value)
    assert info.value.request_id == "req-1"


def test_headers_carry_client_id_scope_and_vendor_types(
    api: respx.MockRouter, us: ProfileClient
) -> None:
    route = api.post(f"{NA}/sp/campaigns/list").mock(
        return_value=Response(200, json={"campaigns": []})
    )
    us.sp.campaigns.list()
    request = route.calls.last.request
    assert request.headers["Amazon-Advertising-API-ClientId"] == "amzn1.client"
    assert request.headers["Amazon-Advertising-API-Scope"] == str(US_PROFILE)
    assert request.headers["Content-Type"] == "application/vnd.spCampaign.v3+json"
    assert request.headers["Accept"] == "application/vnd.spCampaign.v3+json"
    assert request.headers["Authorization"] == "Bearer Atza|test"
    assert json.loads(request.content) == {"stateFilter": {"include": ["ENABLED", "PAUSED"]}}


def test_uk_profile_goes_to_eu_host(api: respx.MockRouter, ads: AmazonAds) -> None:
    from amazon_ads.models import Profile

    uk = ProfileClient(ads, Profile(profile_id=2, country_code="UK"))
    route = api.post("https://advertising-api-eu.amazon.com/sp/campaigns/list").mock(
        return_value=Response(200, json={"campaigns": []})
    )
    uk.sp.campaigns.list()
    assert route.called


def test_token_endpoint_not_called_per_request(api: respx.MockRouter, us: ProfileClient) -> None:
    api.post(f"{NA}/sp/campaigns/list").mock(return_value=Response(200, json={"campaigns": []}))
    for _ in range(3):
        us.sp.campaigns.list()
    assert api.routes["token"].call_count == 1


@pytest.mark.parametrize(
    ("method", "path", "expected"),
    [
        ("GET", "/v2/profiles", True),
        ("POST", "/sp/keywords/list", True),
        ("POST", "/history", True),
        ("POST", "/sp/keywords", False),
        ("PUT", "/sp/keywords", False),
        ("POST", "/sp/keywords/delete", False),
        ("POST", "/reporting/reports", False),
    ],
)
def test_idempotency_rules(method: str, path: str, expected: bool) -> None:
    assert is_idempotent(method, path) is expected


def test_retry_after_accepts_http_date() -> None:
    assert parse_retry_after("Wed, 21 Oct 2015 07:28:10 GMT", now=1445412480.0) == 10.0
    assert parse_retry_after("garbage") is None
