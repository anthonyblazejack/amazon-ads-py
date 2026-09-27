from __future__ import annotations

import pytest
import respx
from httpx import Response

from amazon_ads import AmazonAds, ConfigurationError, ForbiddenError, Region
from tests.conftest import EU, FE, NA


def profile(pid: int, country: str, kind: str = "vendor", name: str = "Author") -> dict:  # type: ignore[type-arg]
    return {
        "profileId": pid,
        "countryCode": country,
        "currencyCode": "USD",
        "accountInfo": {"type": kind, "name": name, "subType": "KDP_AUTHOR"},
    }


def test_profiles_are_merged_across_regions(api: respx.MockRouter, ads: AmazonAds) -> None:
    api.get(f"{NA}/v2/profiles").mock(return_value=Response(200, json=[profile(1, "US")]))
    api.get(f"{EU}/v2/profiles").mock(return_value=Response(200, json=[profile(2, "UK")]))
    api.get(f"{FE}/v2/profiles").mock(return_value=Response(200, json=[profile(3, "AU")]))
    uk = ads.profile("GB")  # ISO spelling accepted for Amazon's "UK"
    assert uk.profile_id == 2
    assert uk.region is Region.EU
    assert ads.profile("AU").region is Region.FE
    assert ads.profile("1").country_code == "US"


def test_one_region_refusing_does_not_hide_the_others(
    api: respx.MockRouter, ads: AmazonAds
) -> None:
    api.get(f"{NA}/v2/profiles").mock(return_value=Response(200, json=[profile(1, "US")]))
    api.get(f"{EU}/v2/profiles").mock(return_value=Response(403, json={"code": "403"}))
    api.get(f"{FE}/v2/profiles").mock(return_value=Response(200, json=[]))
    assert [p.country_code for p in ads.profiles()] == ["US"]


def test_all_regions_refusing_raises(api: respx.MockRouter, ads: AmazonAds) -> None:
    for host in (NA, EU, FE):
        api.get(f"{host}/v2/profiles").mock(return_value=Response(403, json={"code": "403"}))
    with pytest.raises(ForbiddenError):
        ads.profiles()


def test_ambiguous_market_names_the_candidates(api: respx.MockRouter, ads: AmazonAds) -> None:
    api.get(f"{NA}/v2/profiles").mock(
        return_value=Response(
            200, json=[profile(1, "US", "seller", "Shop"), profile(2, "US", "vendor", "Author")]
        )
    )
    api.get(f"{EU}/v2/profiles").mock(return_value=Response(200, json=[]))
    api.get(f"{FE}/v2/profiles").mock(return_value=Response(200, json=[]))
    with pytest.raises(ConfigurationError, match="2 profiles match US"):
        ads.profile("US")
    assert ads.profile("US", account_type="vendor").profile_id == 2
