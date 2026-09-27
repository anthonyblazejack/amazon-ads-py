"""Login with Amazon access tokens.

Access tokens last 60 minutes. :class:`TokenProvider` caches one, refreshes it shortly
before it expires, and is safe to share between threads.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from urllib.parse import urlencode

import httpx

from amazon_ads.config import Credentials
from amazon_ads.errors import AuthError
from amazon_ads.regions import Region

SCOPE = "advertising::campaign_management"

# Refresh this many seconds before Amazon's stated expiry, so a token never expires
# between being handed out and reaching Amazon.
EXPIRY_MARGIN = 120.0


class TokenProvider:
    def __init__(
        self,
        credentials: Credentials,
        *,
        http: httpx.Client | None = None,
        monotonic: Callable[[], float] | None = None,
    ) -> None:
        self._credentials = credentials
        self._http = http or httpx.Client(timeout=30.0)
        self._owns_http = http is None
        self._lock = threading.Lock()
        self._token: str | None = None
        self._expires_at = 0.0
        self._monotonic = monotonic or time.monotonic

    def token(self) -> str:
        with self._lock:
            if self._token is None or self._monotonic() >= self._expires_at:
                self._refresh()
            assert self._token is not None
            return self._token

    def invalidate(self) -> None:
        """Forget the cached token, e.g. after the API answers 401."""
        with self._lock:
            self._token = None
            self._expires_at = 0.0

    def close(self) -> None:
        if self._owns_http:
            self._http.close()

    def _refresh(self) -> None:
        creds = self._credentials
        try:
            response = self._http.post(
                creds.token_url,
                data={
                    "grant_type": "refresh_token",
                    "refresh_token": creds.refresh_token,
                    "client_id": creds.client_id,
                    "client_secret": creds.client_secret,
                },
            )
        except httpx.HTTPError as exc:
            raise AuthError(f"Could not reach {creds.token_url}: {exc}") from exc

        try:
            payload = response.json()
        except ValueError:
            payload = {}
        if response.status_code != 200 or "access_token" not in payload:
            error = payload.get("error")
            description = payload.get("error_description") or response.text[:200]
            hint = ""
            if error == "invalid_grant":
                hint = (
                    " The refresh token is expired or revoked (resetting the client secret"
                    " revokes all of them); repeat the consent to get a new one."
                )
            raise AuthError(f"Token refresh failed: {error}: {description}.{hint}", error=error)

        self._token = str(payload["access_token"])
        expires_in = float(payload.get("expires_in", 3600))
        self._expires_at = self._monotonic() + max(expires_in - EXPIRY_MARGIN, 0.0)


def authorization_url(
    client_id: str, redirect_uri: str, *, region: Region = Region.NA, state: str | None = None
) -> str:
    """The consent URL a person opens to grant this app access to their ads account."""
    params = {
        "client_id": client_id,
        "scope": SCOPE,
        "response_type": "code",
        "redirect_uri": redirect_uri,
    }
    if state:
        params["state"] = state
    return f"{region.authorize_url}?{urlencode(params)}"


def exchange_code(
    code: str,
    *,
    client_id: str,
    client_secret: str,
    redirect_uri: str,
    token_url: str = "https://api.amazon.com/auth/o2/token",
    http: httpx.Client | None = None,
) -> dict[str, object]:
    """Trade a one-time authorization code for tokens.

    The code expires five minutes after consent and works once. The returned dict holds
    ``refresh_token`` (starts with ``Atzr|``), ``access_token`` and ``expires_in``.
    """
    client = http or httpx.Client(timeout=30.0)
    try:
        response = client.post(
            token_url,
            data={
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": redirect_uri,
                "client_id": client_id,
                "client_secret": client_secret,
            },
        )
    finally:
        if http is None:
            client.close()
    payload: dict[str, object] = response.json() if response.content else {}
    if response.status_code != 200 or "refresh_token" not in payload:
        raise AuthError(
            f"Code exchange failed: {payload.get('error')}: {payload.get('error_description')}",
            error=str(payload.get("error")),
        )
    return payload
