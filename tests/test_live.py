"""Read-only checks against the real API. Deselected by default; run with
``AMAZON_ADS_ENV_FILE=/path/to/.env pytest -m live``. Set ``AMAZON_ADS_LIVE_MARKET`` to
choose the marketplace (default US). Nothing here writes to the account."""

from __future__ import annotations

import os
from collections.abc import Iterator
from datetime import UTC, date, datetime, timedelta

import pytest

from amazon_ads import AmazonAds, ProfileClient
from amazon_ads.reports import PRESETS

pytestmark = pytest.mark.live


@pytest.fixture(scope="module")
def live() -> Iterator[ProfileClient]:
    env_file = os.environ.get("AMAZON_ADS_ENV_FILE")
    if not env_file and not os.environ.get("AMAZON_ADS_REFRESH_TOKEN"):
        pytest.skip("No live credentials")
    ads = AmazonAds.from_env(env_file=env_file)
    yield ads.profile(os.environ.get("AMAZON_ADS_LIVE_MARKET", "US"))
    ads.close()


def test_every_sp_entity_lists(live: ProfileClient) -> None:
    for name in (
        "campaigns",
        "ad_groups",
        "keywords",
        "targets",
        "negative_keywords",
        "negative_targets",
        "campaign_negative_keywords",
        "campaign_negative_targets",
        "product_ads",
    ):
        getattr(live.sp, name).list()
    live.portfolios.list()


def test_history_returns_events_for_the_whole_account(live: ProfileClient) -> None:
    events = live.history.list(since=datetime.now(UTC) - timedelta(days=30))
    assert events, "no events in 30 days: either a quiet account or the selector broke"


@pytest.mark.parametrize("preset", sorted(PRESETS))
def test_every_report_preset_is_accepted(live: ProfileClient, preset: str) -> None:
    day = date.today() - timedelta(days=2)
    live.reports.run(preset, day, day, timeout=1800)


def test_suggested_bids_for_enabled_keywords(live: ProfileClient) -> None:
    keywords = live.sp.keywords.list(states=["ENABLED"])[:5]
    if not keywords:
        pytest.skip("No enabled keywords")
    assert live.bids.for_keywords(keywords)
