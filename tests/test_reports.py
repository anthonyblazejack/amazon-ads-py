from __future__ import annotations

import gzip
import json
from datetime import date

import pytest
import respx
from httpx import Request, Response

from amazon_ads import ProfileClient, ReportFailedError
from amazon_ads.reports import Reports, date_chunks, parse_report_file
from tests.conftest import NA

DOWNLOAD = "https://offline-report-storage.s3.amazonaws.com/report"


def test_date_chunks_cover_the_range_without_gaps_or_overlap() -> None:
    chunks = date_chunks(date(2026, 7, 23), date(2026, 9, 25))
    assert chunks == [
        (date(2026, 7, 23), date(2026, 8, 22)),
        (date(2026, 8, 23), date(2026, 9, 22)),
        (date(2026, 9, 23), date(2026, 9, 25)),
    ]
    assert date_chunks(date(2026, 9, 1), date(2026, 9, 1)) == [(date(2026, 9, 1), date(2026, 9, 1))]


def test_run_queues_every_chunk_before_waiting(api: respx.MockRouter, us: ProfileClient) -> None:
    calls: list[str] = []
    created: list[dict] = []  # type: ignore[type-arg]

    def create(request: Request) -> Response:
        body = json.loads(request.content)
        created.append(body)
        calls.append("create")
        return Response(200, json={"reportId": f"r{len(created)}", "status": "PENDING"})

    def status(request: Request) -> Response:
        calls.append("status")
        report_id = request.url.path.rsplit("/", 1)[1]
        return Response(
            200,
            json={"reportId": report_id, "status": "COMPLETED", "url": f"{DOWNLOAD}/{report_id}"},
        )

    def download(request: Request) -> Response:
        report_id = request.url.path.rsplit("/", 1)[1]
        rows = [{"date": "2026-09-01", "campaignId": 1, "cost": 1.5, "chunk": report_id}]
        return Response(200, content=gzip.compress(json.dumps(rows).encode()))

    api.post(f"{NA}/reporting/reports").mock(side_effect=create)
    api.get(url__regex=rf"{NA}/reporting/reports/.+").mock(side_effect=status)
    api.get(url__startswith=DOWNLOAD).mock(side_effect=download)

    with pytest.warns(UserWarning, match="more than 95 days"):
        result = us.reports.run("sp_placement", date(2020, 1, 1), date(2020, 2, 15))

    assert calls[:2] == ["create", "create"]
    assert [r["chunk"] for r in result.rows] == ["r1", "r2"]
    config = created[0]["configuration"]
    assert config["reportTypeId"] == "spCampaigns"
    assert config["groupBy"] == ["campaign", "campaignPlacement"]
    assert config["format"] == "GZIP_JSON"
    assert (created[0]["startDate"], created[0]["endDate"]) == ("2020-01-01", "2020-01-31")


def test_duplicate_request_reuses_the_pending_report(
    api: respx.MockRouter, us: ProfileClient
) -> None:
    api.post(f"{NA}/reporting/reports").mock(
        return_value=Response(
            425,
            json={
                "code": "425",
                "detail": "The Request is a duplicate of : 0f0e5c3a-1111-2222-3333-444455556666",
            },
        )
    )
    config = Reports.configuration("sp_targeting")
    report_id = us.reports.create(config, date(2026, 9, 1), date(2026, 9, 2))
    assert report_id == "0f0e5c3a-1111-2222-3333-444455556666"


def test_failed_report_raises_with_reason(api: respx.MockRouter, us: ProfileClient) -> None:
    api.get(f"{NA}/reporting/reports/r1").mock(
        return_value=Response(
            200, json={"reportId": "r1", "status": "FAILED", "failureReason": "bad column"}
        )
    )
    with pytest.raises(ReportFailedError, match="bad column"):
        us.reports.wait("r1")


def test_wait_backs_off_while_pending(api: respx.MockRouter, us: ProfileClient, sleeps) -> None:  # type: ignore[no-untyped-def]
    api.get(f"{NA}/reporting/reports/r1").mock(
        side_effect=[
            Response(200, json={"reportId": "r1", "status": "PENDING"}),
            Response(200, json={"reportId": "r1", "status": "PROCESSING"}),
            Response(200, json={"reportId": "r1", "status": "COMPLETED", "url": "u"}),
        ]
    )
    assert us.reports.wait("r1", poll=4).status == "COMPLETED"
    assert sleeps == [4, 6]


def test_summary_reports_drop_the_date_column() -> None:
    config = Reports.configuration("sp_campaigns", time_unit="SUMMARY")
    assert "date" not in config["columns"]
    assert config["timeUnit"] == "SUMMARY"


def test_custom_columns_keep_date_for_daily_reports() -> None:
    config = Reports.configuration("sp_targeting", columns=["keywordId", "cost"])
    assert config["columns"] == ["date", "keywordId", "cost"]


def test_report_file_parsing_handles_gzip_plain_and_json_lines() -> None:
    rows = [{"a": 1}, {"a": 2}]
    assert parse_report_file(gzip.compress(json.dumps(rows).encode())) == rows
    assert parse_report_file(b'{"a": 1}\n{"a": 2}\n') == rows
    assert parse_report_file(b"") == []
