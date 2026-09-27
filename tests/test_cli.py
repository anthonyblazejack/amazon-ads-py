from __future__ import annotations

from pathlib import Path

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
