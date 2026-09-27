from __future__ import annotations

from collections.abc import Iterator

import pytest
import respx
from httpx import Response

from amazon_ads import AmazonAds, Credentials, ProfileClient
from amazon_ads.models import Profile

TOKEN_URL = "https://api.amazon.com/auth/o2/token"
NA = "https://advertising-api.amazon.com"
EU = "https://advertising-api-eu.amazon.com"
FE = "https://advertising-api-fe.amazon.com"

# Invented ids. Nothing in this suite comes from a real account.
US_PROFILE = 1111111111111111
UK_PROFILE = 2222222222222222


class Sleeps(list[float]):
    def __call__(self, seconds: float) -> None:
        self.append(seconds)


@pytest.fixture
def api() -> Iterator[respx.MockRouter]:
    with respx.mock(assert_all_called=False) as router:
        router.post(TOKEN_URL, name="token").mock(
            return_value=Response(200, json={"access_token": "Atza|test", "expires_in": 3600})
        )
        yield router


@pytest.fixture
def sleeps() -> Sleeps:
    return Sleeps()


@pytest.fixture
def ads(api: respx.MockRouter, sleeps: Sleeps) -> Iterator[AmazonAds]:
    client = AmazonAds(
        Credentials(client_id="amzn1.client", client_secret="secret", refresh_token="Atzr|x"),
        sleep=sleeps,
    )
    yield client
    client.close()


@pytest.fixture
def us(ads: AmazonAds) -> ProfileClient:
    return ProfileClient(
        ads, Profile(profile_id=US_PROFILE, country_code="US", currency_code="USD")
    )
