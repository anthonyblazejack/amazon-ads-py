# Getting started

## 1. Install

```bash
pip install "amazon-ads-py[cli]"
```

Extras: `cli` (the `adsctl` command), `mcp` (the MCP server), `pandas` (report
DataFrames), `all` (everything). Python 3.11+.

## 2. Get Amazon Ads API access

Amazon grants API access per company, to a Login with Amazon (LwA) client. The steps as
of 2026:

1. **Create an LwA security profile** in the Amazon developer console. It gives you a
   client id (`amzn1.application-oa2-client...`) and a client secret. Add an allowed
   return URL, for example `https://localhost:8400/callback`. Nothing needs to listen
   there. The consent step only needs the address it redirects to.
2. **Apply for Ads API access** at advertising.amazon.com. Direct advertisers apply
   for their own accounts; agencies and tool builders apply as partners. Approval takes
   a few days.
3. **Assign the scope.** Approval does not attach the API scope to your client. In the
   Advanced tools center (My Apps), use "Assign scopes" to put
   `advertising::campaign_management` on your LwA client. This is permanent: the scope
   can live on only one client per company.
4. **Consent.** Sign in as the ads account owner and approve your app:

    ```bash
    adsctl auth url --client-id amzn1.application-oa2-client.xxxx \
                    --redirect-uri https://localhost:8400/callback
    ```

    Open the printed link and click Allow. The browser lands on your return URL with a
    `code=` parameter (the page itself failing to load is expected).

5. **Exchange the code** within five minutes:

    ```bash
    export AMAZON_ADS_CLIENT_ID=... AMAZON_ADS_CLIENT_SECRET=...
    export AMAZON_ADS_REDIRECT_URI=https://localhost:8400/callback
    adsctl auth exchange --write-env .env
    ```

    Paste the whole redirect address when asked. Input is hidden, and the refresh token
    goes straight into `.env` along with today's date as `AMAZON_ADS_CONSENT_DATE`.

!!! warning "Two things that silently break access"
    - Refresh tokens issued since mid 2026 **expire 365 days after consent**, and using
      them does not extend that. `adsctl auth check` shows the date; repeat steps 4 and
      5 before it.
    - **Resetting the client secret revokes every refresh token** issued under it.

## 3. Configure

`.env` (never commit it):

```bash
AMAZON_ADS_CLIENT_ID=amzn1.application-oa2-client.xxxx
AMAZON_ADS_CLIENT_SECRET=xxxx
AMAZON_ADS_REFRESH_TOKEN=Atzr|xxxx
AMAZON_ADS_CONSENT_DATE=2026-09-26
```

`adsctl` reads `./.env` automatically, or the file in `AMAZON_ADS_ENV_FILE`, or
`--env-file`. Variables already set in the environment win over the file.

## 4. First calls

```bash
adsctl auth check        # mints a token, shows the refresh token's expiry
adsctl profiles          # every marketplace the consent covers
adsctl list campaigns -m US
```

```python
from amazon_ads import AmazonAds

ads = AmazonAds.from_env(env_file=".env")
for profile in ads.profiles():
    print(profile.label)

us = ads.profile("US")
print(us.sp.campaigns.list())
```

## Sandbox

Amazon's sandbox host is available with `AmazonAds(..., sandbox=True)` or
`adsctl --sandbox`. It needs a sandbox profile created through Amazon's test account
API (`ops search test account`).
