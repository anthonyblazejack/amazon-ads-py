from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from amazon_ads import Warehouse
from amazon_ads.reports import ReportChunk, ReportResult


def rows(day: str, *pairs: tuple[int, str, float]) -> list[dict[str, Any]]:
    return [
        {"date": day, "campaignId": cid, "placementClassification": place, "cost": cost}
        for cid, place, cost in pairs
    ]


def test_replace_drops_rows_amazon_no_longer_reports(tmp_path) -> None:  # type: ignore[no-untyped-def]
    # A re-pull is the truth for its days: a row that vanished must not linger.
    with Warehouse(tmp_path / "w.sqlite") as store:
        day = date(2026, 9, 1)
        store.replace(
            1, "US", "sp_placement", day, day, rows("2026-09-01", (1, "Top", 1.0), (2, "Top", 2.0))
        )
        store.replace(1, "US", "sp_placement", day, day, rows("2026-09-01", (1, "Top", 1.5)))
        stored = list(store.rows("sp_placement"))
        assert [(r["campaignId"], r["cost"]) for r in stored] == [(1, 1.5)]


def test_days_outside_the_repull_are_kept(tmp_path) -> None:  # type: ignore[no-untyped-def]
    with Warehouse(tmp_path / "w.sqlite") as store:
        store.replace(
            1,
            "US",
            "sp_placement",
            date(2026, 6, 1),
            date(2026, 6, 1),
            rows("2026-06-01", (1, "Top", 3.0)),
        )
        store.replace(
            1,
            "US",
            "sp_placement",
            date(2026, 9, 1),
            date(2026, 9, 1),
            rows("2026-09-01", (1, "Top", 1.0)),
        )
        assert [r["date"] for r in store.rows("sp_placement")] == ["2026-06-01", "2026-09-01"]


def test_rows_sharing_a_key_on_one_day_both_survive(tmp_path) -> None:  # type: ignore[no-untyped-def]
    with Warehouse(tmp_path / "w.sqlite") as store:
        day = date(2026, 9, 1)
        store.replace(
            1, "US", "sp_placement", day, day, rows("2026-09-01", (1, "Top", 1.0), (1, "Top", 2.0))
        )
        assert len(list(store.rows("sp_placement"))) == 2


def test_views_expose_report_columns_to_plain_sql(tmp_path) -> None:  # type: ignore[no-untyped-def]
    with Warehouse(tmp_path / "w.sqlite") as store:
        day = date(2026, 9, 1)
        store.replace(
            1, "US", "sp_placement", day, day, rows("2026-09-01", (1, "Top", 1.0), (2, "Top", 2.5))
        )
        [total] = store.query(
            "SELECT placementClassification, SUM(cost) AS cost FROM v_sp_placement GROUP BY 1"
        )
        assert total == {"placementClassification": "Top", "cost": 3.5}


def test_query_refuses_writes(tmp_path) -> None:  # type: ignore[no-untyped-def]
    import pytest

    store = Warehouse(tmp_path / "w.sqlite")
    with pytest.raises(ValueError, match="only runs SELECT"):
        store.query("DELETE FROM report_rows")
    store.close()


class FakeReports:
    def __init__(self) -> None:
        self.ranges: list[tuple[date, date]] = []

    def run(self, report: str, start: date, end: date) -> ReportResult:
        self.ranges.append((start, end))
        return ReportResult(
            rows=[], chunks=[ReportChunk(start, end, "r")], configuration={}, start=start, end=end
        )


class FakeClient:
    profile_id = 1
    country_code = "US"

    def __init__(self) -> None:
        self.reports = FakeReports()


def test_sync_resumes_42_days_before_last_pull_and_first_run_reaches_retention(tmp_path) -> None:  # type: ignore[no-untyped-def]
    client = FakeClient()
    yesterday = date.today() - timedelta(days=1)
    with Warehouse(tmp_path / "w.sqlite") as store:
        store.sync(client, ["sp_placement"])  # type: ignore[arg-type]
        first_start, first_end = client.reports.ranges[0]
        assert first_start == date.today() - timedelta(days=94)
        assert first_end == yesterday

        store.sync(client, ["sp_placement"])  # type: ignore[arg-type]
        second_start, _ = client.reports.ranges[1]
        assert second_start == yesterday - timedelta(days=42)

        store.sync(client, ["sp_search_terms"])  # type: ignore[arg-type]
        assert client.reports.ranges[2][0] == date.today() - timedelta(days=64)
