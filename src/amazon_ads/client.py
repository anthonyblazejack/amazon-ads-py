"""The entry points: :class:`AmazonAds` for the account, :class:`ProfileClient` per market."""

from __future__ import annotations

import threading
import time
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import TYPE_CHECKING, Any

import httpx

from amazon_ads import catalog
from amazon_ads.auth import TokenProvider
from amazon_ads.config import Credentials
from amazon_ads.errors import ApiError, ConfigurationError
from amazon_ads.models import Profile
from amazon_ads.regions import Region, normalize_country, region_for
from amazon_ads.transport import RetryPolicy, Transport

if TYPE_CHECKING:
    from amazon_ads.bids import BidRecommendations
    from amazon_ads.history import History
    from amazon_ads.reports import Reports
    from amazon_ads.sp import SponsoredProducts
    from amazon_ads.sp.resource import SpResource


class AmazonAds:
    """Account-level client. Lists profiles and hands out a :class:`ProfileClient` per
    marketplace.

    >>> ads = AmazonAds.from_env()
    >>> us = ads.profile("US")
    >>> us.sp.keywords.list(campaign_ids=["123"])
    """

    def __init__(
        self,
        credentials: Credentials,
        *,
        sandbox: bool = False,
        retry: RetryPolicy | None = None,
        timeout: float = 60.0,
        http: httpx.Client | None = None,
        user_agent: str | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.credentials = credentials
        self.sleep = sleep
        self._http = http or httpx.Client(timeout=timeout)
        self._owns_http = http is None
        self.tokens = TokenProvider(credentials, http=self._http)
        self.transport = Transport(
            self.tokens,
            credentials.client_id,
            http=self._http,
            retry=retry,
            sandbox=sandbox,
            user_agent=user_agent,
            sleep=sleep,
        )
        self._profiles: list[Profile] | None = None
        self._profile_lock = threading.Lock()
        self._clients: dict[int, ProfileClient] = {}

    @classmethod
    def from_env(cls, *, env_file: str | Path | None = None, **kwargs: Any) -> AmazonAds:
        """Build a client from ``AMAZON_ADS_*`` environment variables (and optionally a
        dotenv file). See :meth:`Credentials.from_env`."""
        return cls(Credentials.from_env(env_file=env_file), **kwargs)

    def __enter__(self) -> AmazonAds:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def close(self) -> None:
        if self._owns_http:
            self._http.close()

    # --- profiles -----------------------------------------------------------------------

    def profiles(self, *, refresh: bool = False) -> list[Profile]:
        """Every profile the token can reach, across all three regions.

        Amazon only returns a region's profiles from that region's host, so this asks all
        three and merges the answers. Cached after the first call.
        """
        with self._profile_lock:
            if self._profiles is None or refresh:
                seen: dict[int, Profile] = {}
                failures: list[ApiError] = []
                for region in Region:
                    try:
                        response = self.transport.request("GET", "/v2/profiles", region=region)
                    except ApiError as exc:
                        # A consent can be limited to some regions; one region refusing
                        # should not hide the profiles the others return.
                        failures.append(exc)
                        continue
                    for raw in response.json():
                        profile = Profile.model_validate(raw)
                        seen.setdefault(profile.profile_id, profile)
                if failures and len(failures) == len(Region):
                    raise failures[0]
                self._profiles = sorted(seen.values(), key=lambda p: (p.country_code, p.profile_id))
            return list(self._profiles)

    def profile(
        self,
        market_or_id: str | int,
        *,
        account_type: str | None = None,
        account_name: str | None = None,
    ) -> ProfileClient:
        """A client scoped to one profile.

        ``market_or_id`` is a country code (``"US"``, ``"UK"`` or ``"GB"``) or a profile id.
        When one marketplace holds several profiles (a seller and a vendor account, say),
        narrow it with ``account_type`` (``"seller"``, ``"vendor"``, ``"agency"``) or
        ``account_name``.
        """
        profile = self._resolve(market_or_id, account_type, account_name)
        client = self._clients.get(profile.profile_id)
        if client is None:
            client = ProfileClient(self, profile)
            self._clients[profile.profile_id] = client
        return client

    def profile_from(self, profile_id: int, country_code: str) -> ProfileClient:
        """A client for a known profile without listing profiles first (saves a call)."""
        profile = Profile(profile_id=profile_id, country_code=normalize_country(country_code))
        client = ProfileClient(self, profile)
        self._clients.setdefault(profile_id, client)
        return client

    def _resolve(
        self, market_or_id: str | int, account_type: str | None, account_name: str | None
    ) -> Profile:
        profiles = self.profiles()
        key = str(market_or_id).strip()
        if key.isdigit():
            for profile in profiles:
                if profile.profile_id == int(key):
                    return profile
            raise ConfigurationError(f"No profile with id {key} is reachable with this token")

        code = normalize_country(key)
        candidates = [p for p in profiles if p.country_code == code]
        if account_type:
            candidates = [
                p
                for p in candidates
                if p.account_info and (p.account_info.type or "").lower() == account_type.lower()
            ]
        if account_name:
            candidates = [
                p for p in candidates if p.account_info and p.account_info.name == account_name
            ]
        if len(candidates) == 1:
            return candidates[0]
        if not candidates:
            available = ", ".join(sorted({p.country_code for p in profiles})) or "none"
            raise ConfigurationError(f"No profile for {code}. Profiles exist for: {available}")
        options = "; ".join(p.label for p in candidates)
        raise ConfigurationError(
            f"{len(candidates)} profiles match {code}; pass account_type, account_name or a "
            f"profile id. Candidates: {options}"
        )


class ProfileClient:
    """Everything scoped to one advertising profile (one account in one marketplace).

    Attributes:
        sp: Sponsored Products campaigns, ad groups, keywords, targets, negatives, ads.
        portfolios: Portfolios.
        bids: Suggested bids.
        history: Change history.
        reports: Async reports (reporting v3).
    """

    def __init__(self, account: AmazonAds, profile: Profile) -> None:
        from amazon_ads.bids import BidRecommendations
        from amazon_ads.history import History
        from amazon_ads.reports import Reports
        from amazon_ads.sp import PORTFOLIOS, SponsoredProducts
        from amazon_ads.sp.resource import SpResource

        self.account = account
        self.profile = profile
        self.region: Region = region_for(profile.country_code)
        self.sp: SponsoredProducts = SponsoredProducts(self)
        self.portfolios: SpResource[Any] = SpResource(self, PORTFOLIOS)
        self.bids: BidRecommendations = BidRecommendations(self)
        self.history: History = History(self)
        self.reports: Reports = Reports(self, sleep=account.sleep)

    def __repr__(self) -> str:
        return f"<ProfileClient {self.country_code} {self.profile_id}>"

    @property
    def profile_id(self) -> int:
        return self.profile.profile_id

    @property
    def country_code(self) -> str:
        return self.profile.country_code

    @property
    def currency(self) -> str | None:
        return self.profile.currency_code

    def request(
        self,
        method: str,
        path: str,
        *,
        json: Any = None,
        params: Mapping[str, Any] | None = None,
        content_type: str | None = None,
        accept: str | None = None,
        headers: Mapping[str, str] | None = None,
        idempotent: bool | None = None,
    ) -> httpx.Response:
        """A raw call on this profile's regional host with the profile scope header."""
        return self.account.transport.request(
            method,
            path,
            region=self.region,
            profile_id=self.profile_id,
            json=json,
            params=params,
            content_type=content_type,
            accept=accept,
            headers=headers,
            idempotent=idempotent,
        )

    def call(
        self,
        operation: str,
        body: Any = None,
        *,
        path_params: Mapping[str, Any] | None = None,
        query: Mapping[str, Any] | None = None,
        headers: Mapping[str, str] | None = None,
        content_type: str | None = None,
        accept: str | None = None,
    ) -> Any:
        """Send any operation from Amazon's published specs, by id.

        >>> us.call("sponsored-products:ListSponsoredProductsKeywords",
        ...         {"campaignIdFilter": {"include": ["123"]}})

        The method, path and vendor media types come from the operation catalog (see
        :mod:`amazon_ads.catalog`). Returns the parsed JSON body, or ``None`` for an empty
        response. 207 bodies are returned as-is; pass them to
        :func:`amazon_ads.batch.parse_multi_status` for per-item results.
        """
        op = catalog.get(operation)
        path = op.format_path(dict(path_params or {}))
        chosen_type = content_type or (op.content_type if op.has_body else None)
        response = self.request(
            op.method,
            path,
            json=body,
            params=query,
            content_type=chosen_type,
            accept=accept or op.accept,
            headers=headers,
            idempotent=op.read_only,
        )
        if not response.content:
            return None
        try:
            return response.json()
        except ValueError:
            return response.text

    def resource(self, kind: str) -> SpResource[Any]:
        """The resource behind a change plan's ``kind`` (``"sp.keywords"``, ...)."""
        from amazon_ads.sp import ALL_SPECS
        from amazon_ads.sp.resource import SpResource

        for spec in ALL_SPECS:
            if spec.kind == kind:
                return SpResource(self, spec)
        raise KeyError(f"Unknown resource kind {kind!r}")
