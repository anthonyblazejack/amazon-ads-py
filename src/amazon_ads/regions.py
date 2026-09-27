"""Amazon Ads regions and the marketplaces that belong to each.

Every Ads API call goes to one of three regional hosts, chosen by the marketplace the
profile belongs to. Sending a UK profile's request to the North America host returns
an error, so the client picks the host from the profile's country code.
"""

from __future__ import annotations

from enum import StrEnum


class Region(StrEnum):
    NA = "NA"
    EU = "EU"
    FE = "FE"

    @property
    def api_host(self) -> str:
        return _API_HOSTS[self]

    @property
    def base_url(self) -> str:
        return f"https://{self.api_host}"

    @property
    def token_url(self) -> str:
        """Login with Amazon token endpoint Amazon documents for this region.

        An access token minted by any of these endpoints is accepted by all three regional
        API hosts, so the client uses a single token endpoint (``api.amazon.com`` unless
        configured otherwise) and does not need one token per region.
        """
        return _TOKEN_URLS[self]

    @property
    def authorize_url(self) -> str:
        return _AUTHORIZE_URLS[self]


_API_HOSTS = {
    Region.NA: "advertising-api.amazon.com",
    Region.EU: "advertising-api-eu.amazon.com",
    Region.FE: "advertising-api-fe.amazon.com",
}

_TOKEN_URLS = {
    Region.NA: "https://api.amazon.com/auth/o2/token",
    Region.EU: "https://api.amazon.co.uk/auth/o2/token",
    Region.FE: "https://api.amazon.co.jp/auth/o2/token",
}

_AUTHORIZE_URLS = {
    Region.NA: "https://www.amazon.com/ap/oa",
    Region.EU: "https://eu.account.amazon.com/ap/oa",
    Region.FE: "https://apac.account.amazon.com/ap/oa",
}

SANDBOX_HOST = "advertising-api-test.amazon.com"

# Country codes as Amazon reports them in a profile's countryCode. Amazon uses "UK", not
# the ISO "GB", for the United Kingdom.
MARKETPLACE_REGIONS: dict[str, Region] = {
    "US": Region.NA,
    "CA": Region.NA,
    "MX": Region.NA,
    "BR": Region.NA,
    "UK": Region.EU,
    "DE": Region.EU,
    "FR": Region.EU,
    "IT": Region.EU,
    "ES": Region.EU,
    "NL": Region.EU,
    "SE": Region.EU,
    "PL": Region.EU,
    "BE": Region.EU,
    "IE": Region.EU,
    "TR": Region.EU,
    "AE": Region.EU,
    "SA": Region.EU,
    "EG": Region.EU,
    "IN": Region.EU,
    "ZA": Region.EU,
    "JP": Region.FE,
    "AU": Region.FE,
    "SG": Region.FE,
}


def normalize_country(code: str) -> str:
    """Return Amazon's spelling of a country code (``GB`` becomes ``UK``)."""
    upper = code.strip().upper()
    return "UK" if upper == "GB" else upper


def region_for(country_code: str) -> Region:
    """Return the region that serves a marketplace, e.g. ``region_for("AU") is Region.FE``."""
    code = normalize_country(country_code)
    try:
        return MARKETPLACE_REGIONS[code]
    except KeyError:
        known = ", ".join(sorted(MARKETPLACE_REGIONS))
        raise ValueError(f"Unknown marketplace {country_code!r}. Known: {known}") from None
