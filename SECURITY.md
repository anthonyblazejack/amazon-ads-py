# Security

Please report vulnerabilities privately through GitHub's
[security advisories](https://github.com/anthonyblazejack/amazon-ads-py/security/advisories/new)
rather than a public issue.

## Handling credentials

- The library reads credentials from the environment or a dotenv file you point it at.
  It never writes them anywhere except when you run `adsctl auth exchange --write-env`.
- `Credentials` hides the client secret and refresh token from `repr()`.
- A refresh token gives full campaign management access to every profile the consent
  covers. Treat it like a password. Revoke it by resetting the client secret, which
  revokes every refresh token issued under that client.
- The MCP server cannot apply a change without the fingerprint of a plan that was shown
  to the user, and `AMAZON_ADS_MCP_READ_ONLY=1` removes write tools entirely.
