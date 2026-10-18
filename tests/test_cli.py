from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
import respx
from click.testing import CliRunner

from amazon_ads import UnsupportedOperationError
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


def test_ops_call_refuses_an_mcp_tool_without_asking(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The write confirmation would ask to send a tool Amazon's Ads API hosts cannot
    serve; it must be refused before the prompt and before any credentials are read. No
    answer is fed to the prompt and no credentials are reachable, so a regression fails
    here instead of reaching Amazon."""
    monkeypatch.delenv("AMAZON_ADS_ENV_FILE", raising=False)
    monkeypatch.chdir(tmp_path)
    result = CliRunner().invoke(
        cli, ["ops", "call", "mcp-account-management:update_account_name", "-m", "US"]
    )
    assert isinstance(result.exception, UnsupportedOperationError)
    assert "Send it?" not in result.output


def test_ops_search_lists_mcp_tools_only_when_asked() -> None:
    hidden = CliRunner().invoke(cli, ["--json", "ops", "search", "list", "invoices"])
    assert hidden.exit_code == 0, hidden.output
    assert "mcp-billing:list_invoices" not in hidden.output
    found = CliRunner().invoke(cli, ["--json", "ops", "search", "invoices"])
    rows = {r["id"]: r for r in json.loads(found.output)}
    assert "406" in rows["advertising-billing:getAdvertiserInvoices"]["note"]
    shown = CliRunner().invoke(
        cli, ["--json", "ops", "search", "list", "invoices", "--include-unsupported"]
    )
    assert "mcp-billing:list_invoices" in shown.output
    assert "hosted Ads MCP server" in shown.output


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


@pytest.mark.parametrize(
    ("args", "option", "reason"),
    [
        pytest.param(
            ["-p", "reportId=../../sb/keywords/123"],
            "--path-param",
            "contains '/'",
            id="leaves-segment",
        ),
        pytest.param([], "--path-param", "needs path parameters", id="missing"),
        pytest.param(["-p", "reportId"], "--path-param", "is not name=value", id="no-equals"),
        pytest.param(["-p", "=r-1"], "--path-param", "is not name=value", id="no-name"),
        pytest.param(
            ["-p", "reportId=r-1", "-p", "reportId=r-2"],
            "--path-param",
            "given twice",
            id="twice",
        ),
        pytest.param(
            ["-p", "reportId=r-1", "-q", "x"], "--query", "is not name=value", id="query-no-equals"
        ),
        pytest.param(
            ["-p", "reportId=r-1", "-q", "=x"], "--query", "is not name=value", id="query-no-name"
        ),
    ],
)
def test_ops_call_refuses_bad_parameters_before_asking_or_sending(
    api: respx.MockRouter,
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    args: list[str],
    option: str,
    reason: str,
) -> None:
    # Each of these used to surface as a Python traceback, after the write confirmation
    # had been answered and after the profile lookup had called Amazon; a name given
    # twice silently kept the last value. "../../sb/keywords/123" would have turned the
    # confirmed DELETE of a report into DELETE /sb/keywords/123. No answer is fed to the
    # prompt, and the respx router and empty credentials keep a regression from reaching
    # Amazon.
    for name in [n for n in os.environ if n.startswith("AMAZON_ADS_")]:
        monkeypatch.delenv(name)
    empty_env = tmp_path / "empty.env"
    empty_env.write_text("")
    result = CliRunner().invoke(
        cli,
        [
            *("--env-file", str(empty_env), "ops", "call", "reporting:deleteAsyncReport"),
            *("-m", "US", *args),
        ],
    )
    assert result.exit_code == 2, result.output
    assert option in result.output
    assert reason in result.output
    assert "Send it?" not in result.output
    assert not api.calls
