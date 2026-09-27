# Authentication, profiles and regions

## Credentials

```python
from amazon_ads import AmazonAds, Credentials

ads = AmazonAds(Credentials(client_id="...", client_secret="...", refresh_token="Atzr|..."))
ads = AmazonAds.from_env()                   # AMAZON_ADS_* from the environment
ads = AmazonAds.from_env(env_file=".env")    # ...or a dotenv file (environment wins)
```

| Variable | Required | Meaning |
|---|---|---|
| `AMAZON_ADS_CLIENT_ID` | yes | LwA client id |
| `AMAZON_ADS_CLIENT_SECRET` | yes | LwA client secret |
| `AMAZON_ADS_REFRESH_TOKEN` | yes | From the consent (`Atzr|...`) |
| `AMAZON_ADS_CONSENT_DATE` | no | `YYYY-MM-DD`; enables expiry warnings |
| `AMAZON_ADS_TOKEN_URL` | no | Defaults to `https://api.amazon.com/auth/o2/token` |

`Credentials.refresh_token_expires` is the consent date plus 365 days. Secrets are
hidden from `repr()`.

Access tokens last an hour. The client caches one, refreshes it two minutes before
expiry, and refreshes once more if Amazon answers 401. One access token works on all
three regional hosts.

## Profiles

A profile is one advertiser account in one marketplace. Every call except listing
profiles is scoped to one.

```python
ads.profiles()                          # all profiles, all regions, cached
us = ads.profile("US")                  # by country code ("GB" is accepted for "UK")
us = ads.profile(1234567890123456)      # by profile id
us = ads.profile("US", account_type="seller")   # when a market has several accounts
us = ads.profile_from(1234567890123456, "US")   # known id: skips listing profiles
```

Amazon only returns a region's profiles from that region's host, so `profiles()` asks
all three and merges them. If one region refuses (a consent limited to some regions),
the others are still returned.

## Regions

| Region | Host | Marketplaces |
|---|---|---|
| NA | `advertising-api.amazon.com` | US, CA, MX, BR |
| EU | `advertising-api-eu.amazon.com` | UK, DE, FR, IT, ES, NL, SE, PL, BE, IE, TR, AE, SA, EG, IN, ZA |
| FE | `advertising-api-fe.amazon.com` | JP, AU, SG |

`ProfileClient.region` is picked from the profile's country code, so you never choose a
host yourself.

## A profile client

`ads.profile(...)` returns a `ProfileClient`:

| Attribute | What |
|---|---|
| `sp.campaigns`, `sp.ad_groups`, `sp.keywords`, `sp.targets`, `sp.negative_keywords`, `sp.negative_targets`, `sp.campaign_negative_keywords`, `sp.campaign_negative_targets`, `sp.product_ads` | [Sponsored Products](campaign-management.md) |
| `portfolios` | Portfolios (same interface) |
| `bids` | [Suggested bids](suggested-bids.md) |
| `history` | [Change history](change-history.md) |
| `reports` | [Reports](reports.md) |
| `call(operation_id, body, ...)` | [Any operation](any-endpoint.md) |
| `request(method, path, ...)` | A raw request with auth, scope and retries |
