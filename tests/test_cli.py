from __future__ import annotations

import os
from pathlib import Path

import pytest
import respx
from click.testing import CliRunner

from amazon_ads.cli import _read_changes, cli, table


def test_csv_bids_become_numbers_and_ids_stay_strings(tmp_path: Path) -> None:
    path = tmp_path / "changes.csv"
    path.write_text("keywordId,bid,state\n0123,1,enabled\n")
    assert _read_changes(path) == [{"keywordId": "0123", "bid": 1.0, "state": "ENABLED"}]


def test_table_output_handles_nested_values() -> None:
    text = table([{"id": "1", "budget": {"budget": 10}}])
    assert '{"budget":10}' in text
    assert "(1 rows)" in text


def test_ops_search_needs_no_credentials() -> None:
    result = CliRunner().invoke(cli, ["--json", "ops", "search", "keywords", "list"])
    assert result.exit_code == 0, result.output
    assert "/sp/keywords/list" in result.output


def test_ops_call_asks_before_creating_targets(
    api: respx.MockRouter, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    # POST /sp/targets once counted as read-only because "get" is a substring of
    # "targets", so `ops call` sent the create without asking. Declining must stop it
    # before a request is sent. The respx router and the empty credentials keep a
    # regression from ever reaching Amazon with a developer's real account.
    for name in [n for n in os.environ if n.startswith("AMAZON_ADS_")]:
        monkeypatch.delenv(name)
    empty_env = tmp_path / "empty.env"
    empty_env.write_text("")
    result = CliRunner().invoke(
        cli,
        [
            "--env-file",
            str(empty_env),
            "ops",
            "call",
            "sponsored-products:CreateSponsoredProductsTargetingClauses",
            "-m",
            "US",
        ],
        input="n\n",
    )
    assert "can change data. Send it?" in result.output
    assert result.exit_code == 1
    assert not api.calls
