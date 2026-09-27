"""Credentials and client settings."""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path

from amazon_ads.errors import ConfigurationError

# Amazon fixes Login with Amazon refresh tokens issued on or after 2026-07-30 at 365 days
# from the consent, and refreshing does not extend them.
REFRESH_TOKEN_LIFETIME = timedelta(days=365)

ENV_PREFIX = "AMAZON_ADS_"


@dataclass(frozen=True)
class Credentials:
    """What the client needs to authenticate.

    ``consent_date`` is optional. When set, :attr:`refresh_token_expires` tells you when
    the consent has to be repeated, and the CLI warns 30 days ahead.
    """

    client_id: str
    client_secret: str = field(repr=False)
    refresh_token: str = field(repr=False)
    token_url: str = "https://api.amazon.com/auth/o2/token"
    consent_date: date | None = None

    def __post_init__(self) -> None:
        missing = [
            name
            for name in ("client_id", "client_secret", "refresh_token")
            if not getattr(self, name)
        ]
        if missing:
            raise ConfigurationError(f"Missing credentials: {', '.join(missing)}")

    @property
    def refresh_token_expires(self) -> date | None:
        if self.consent_date is None:
            return None
        return self.consent_date + REFRESH_TOKEN_LIFETIME

    @classmethod
    def from_env(
        cls, prefix: str = ENV_PREFIX, *, env_file: str | Path | None = None
    ) -> Credentials:
        """Read ``AMAZON_ADS_CLIENT_ID``, ``_CLIENT_SECRET``, ``_REFRESH_TOKEN`` and the
        optional ``_TOKEN_URL`` and ``_CONSENT_DATE`` (YYYY-MM-DD).

        ``env_file`` points at a dotenv file to read first. Variables already set in the
        process environment win over the file.
        """
        values: dict[str, str] = {}
        if env_file is not None:
            values.update(read_env_file(env_file))
        values.update({k: v for k, v in os.environ.items() if k.startswith(prefix)})

        def get(name: str) -> str:
            return values.get(prefix + name, "").strip()

        consent = get("CONSENT_DATE")
        try:
            consent_date = date.fromisoformat(consent) if consent else None
        except ValueError:
            raise ConfigurationError(
                f"{prefix}CONSENT_DATE must be YYYY-MM-DD, got {consent!r}"
            ) from None

        return cls(
            client_id=get("CLIENT_ID"),
            client_secret=get("CLIENT_SECRET"),
            refresh_token=get("REFRESH_TOKEN"),
            token_url=get("TOKEN_URL") or cls.token_url,
            consent_date=consent_date,
        )


def read_env_file(path: str | Path) -> dict[str, str]:
    """Parse a dotenv file: ``KEY=value`` lines, ``#`` comments, optional quotes."""
    result: dict[str, str] = {}
    p = Path(path).expanduser()
    if not p.is_file():
        raise ConfigurationError(f"Env file not found: {p}")
    for raw in p.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[len("export ") :]
        key, _, value = line.partition("=")
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        result[key.strip()] = value
    return result
