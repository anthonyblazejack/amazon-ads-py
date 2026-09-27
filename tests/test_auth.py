from __future__ import annotations

from datetime import date

import httpx
import pytest
import respx
from httpx import Response

from amazon_ads import AuthError, ConfigurationError, Credentials
from amazon_ads.auth import TokenProvider, authorization_url
from amazon_ads.config import read_env_file
from tests.conftest import TOKEN_URL


class Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


CREDS = Credentials(client_id="c", client_secret="s", refresh_token="r")


def test_token_is_cached_until_near_expiry() -> None:
    clock = Clock()
    with respx.mock() as router:
        route = router.post(TOKEN_URL).mock(
            side_effect=[
                Response(200, json={"access_token": "one", "expires_in": 3600}),
                Response(200, json={"access_token": "two", "expires_in": 3600}),
            ]
        )
        tokens = TokenProvider(CREDS, http=httpx.Client(), monotonic=clock)
        assert tokens.token() == "one"
        clock.now = 3000  # well before the refresh point (expiry minus the 120s margin)
        assert tokens.token() == "one"
        clock.now = 3500  # inside the margin: refresh before Amazon would reject it
        assert tokens.token() == "two"
        assert route.call_count == 2


def test_invalid_grant_explains_expired_consent() -> None:
    with respx.mock() as router:
        router.post(TOKEN_URL).mock(
            return_value=Response(
                400, json={"error": "invalid_grant", "error_description": "expired"}
            )
        )
        tokens = TokenProvider(CREDS, http=httpx.Client())
        with pytest.raises(AuthError) as info:
            tokens.token()
    assert info.value.error == "invalid_grant"
    assert "repeat the consent" in str(info.value)


def test_missing_credentials_fail_fast() -> None:
    with pytest.raises(ConfigurationError, match="refresh_token"):
        Credentials(client_id="c", client_secret="s", refresh_token="")


def test_env_file_is_read_and_process_env_wins(tmp_path, monkeypatch) -> None:  # type: ignore[no-untyped-def]
    env = tmp_path / ".env"
    env.write_text(
        "# comment\n"
        "AMAZON_ADS_CLIENT_ID=from-file\n"
        'AMAZON_ADS_CLIENT_SECRET="quoted secret"\n'
        "export AMAZON_ADS_REFRESH_TOKEN=Atzr|abc\n"
        "AMAZON_ADS_CONSENT_DATE=2026-09-26\n"
    )
    monkeypatch.setenv("AMAZON_ADS_CLIENT_ID", "from-env")
    creds = Credentials.from_env(env_file=env)
    assert creds.client_id == "from-env"
    assert creds.client_secret == "quoted secret"
    assert creds.refresh_token == "Atzr|abc"
    assert creds.refresh_token_expires == date(2027, 9, 26)
    assert read_env_file(env)["AMAZON_ADS_CLIENT_ID"] == "from-file"


def test_secrets_do_not_appear_in_repr() -> None:
    text = repr(Credentials(client_id="c", client_secret="hunter2", refresh_token="Atzr|zz"))
    assert "hunter2" not in text
    assert "Atzr" not in text


def test_authorization_url_requests_the_ads_scope() -> None:
    url = authorization_url("client", "https://localhost:8400/callback")
    assert url.startswith("https://www.amazon.com/ap/oa?")
    assert "scope=advertising%3A%3Acampaign_management" in url
