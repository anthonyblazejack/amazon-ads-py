"""Reporting v3: request a report, wait for it, download and parse it, in one call.

Amazon's reports are asynchronous: ``POST /reporting/reports`` queues one, ``GET
/reporting/reports/{id}`` is polled until ``COMPLETED``, then a pre-signed URL serves a
gzipped JSON file. :meth:`Reports.run` does all of that and also:

* splits ranges longer than 31 days (Amazon's per-report maximum) into chunks, queues
  every chunk before waiting on any, and returns the rows of all of them together,
* reuses the pending report when Amazon answers 425 "duplicate of <reportId>" instead of
  failing,
* warns when a range reaches past Amazon's retention (about 95 days; 65 for search terms).

Every preset in :data:`PRESETS` (``sp_*`` for Sponsored Products, ``sb_*`` for Sponsored
Brands) was accepted by Amazon's live API for a KDP author profile in September 2026.
Pass ``columns=`` to change them, or a full ``configuration`` dict for anything else
(Sponsored Display, DSP).
"""

from __future__ import annotations

import gzip
import json
import logging
import re
import time
import warnings
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import TYPE_CHECKING, Any

from amazon_ads.errors import ApiError, ReportFailedError

if TYPE_CHECKING:
    from amazon_ads.client import ProfileClient

log = logging.getLogger("amazon_ads.reports")

CREATE_TYPE = "application/vnd.createasyncreportrequest.v3+json"
CREATE_ACCEPT = "application/vnd.createasyncreportresponse.v3+json"
GET_ACCEPT = "application/vnd.getasyncreportresponse.v3+json"
DELETE_ACCEPT = "application/vnd.deleteasyncreportresponse.v3+json"
MAX_DAYS = 31

_METRICS_1_7_14 = [
    "impressions",
    "clicks",
    "cost",
    "purchases1d",
    "purchases7d",
    "purchases14d",
    "sales1d",
    "sales7d",
    "sales14d",
    "unitsSoldClicks14d",
    "kindleEditionNormalizedPagesRead14d",
    "kindleEditionNormalizedPagesRoyalties14d",
]

# Sponsored Brands reports have one fixed attribution window (14 days), so the metrics
# carry no 1d/7d/14d suffix. Each SB report type accepts a different subset: sbTargeting
# rejects the Kindle page columns and sbSearchTerm also rejects detailPageViews and the
# new-to-brand columns.
_SB_METRICS = ["impressions", "clicks", "cost", "purchases", "sales", "unitsSold"]
# Amazon keeps 60 days of every Sponsored Brands report type; an older start date answers
# 400 "must be equal to or after report type data retention start date".
_SB_RETENTION_DAYS = 60


@dataclass(frozen=True)
class ReportPreset:
    report_type_id: str
    group_by: tuple[str, ...]
    columns: tuple[str, ...]
    # Columns that identify a row within a day. Names are left out because a renamed
    # campaign would otherwise look like a new row.
    key_columns: tuple[str, ...] = ()
    ad_product: str = "SPONSORED_PRODUCTS"
    retention_days: int = 95
    description: str = ""


PRESETS: dict[str, ReportPreset] = {
    "sp_campaigns": ReportPreset(
        "spCampaigns",
        ("campaign",),
        (
            "date",
            "campaignId",
            "campaignName",
            "campaignStatus",
            "campaignBudgetAmount",
            "campaignBudgetType",
            "campaignBudgetCurrencyCode",
            "campaignBiddingStrategy",
            "topOfSearchImpressionShare",
            *_METRICS_1_7_14,
        ),
        key_columns=("campaignId",),
        description="One row per campaign per day.",
    ),
    "sp_placement": ReportPreset(
        "spCampaigns",
        ("campaign", "campaignPlacement"),
        (
            "date",
            "campaignId",
            "campaignName",
            "campaignStatus",
            "campaignBudgetAmount",
            "campaignBiddingStrategy",
            "placementClassification",
            *_METRICS_1_7_14,
        ),
        key_columns=("campaignId", "placementClassification"),
        description="One row per campaign, placement (top of search, rest of search, "
        "product pages) and day.",
    ),
    "sp_ad_groups": ReportPreset(
        "spCampaigns",
        ("adGroup",),
        # Amazon rejects campaignId and campaignName when grouping by ad group.
        (
            "date",
            "adGroupId",
            "adGroupName",
            "adStatus",
            *_METRICS_1_7_14,
        ),
        key_columns=("adGroupId",),
        description="One row per ad group per day.",
    ),
    "sp_targeting": ReportPreset(
        "spTargeting",
        ("targeting",),
        (
            "date",
            "campaignId",
            "campaignName",
            "adGroupId",
            "adGroupName",
            "keywordId",
            "keyword",
            "targeting",
            "keywordType",
            "matchType",
            "keywordBid",
            "adKeywordStatus",
            "topOfSearchImpressionShare",
            *_METRICS_1_7_14,
        ),
        key_columns=("adGroupId", "keywordId"),
        description="One row per keyword or product target per day. keywordId holds the "
        "target id for product targets. keywordBid is the bid at pull time.",
    ),
    "sp_search_terms": ReportPreset(
        "spSearchTerm",
        ("searchTerm",),
        (
            "date",
            "campaignId",
            "campaignName",
            "adGroupId",
            "adGroupName",
            "keywordId",
            "keyword",
            "targeting",
            "matchType",
            "keywordType",
            "searchTerm",
            *_METRICS_1_7_14,
        ),
        retention_days=65,
        key_columns=("adGroupId", "keywordId", "searchTerm"),
        description="One row per customer search term per target per day. Only terms with "
        "at least one click are included.",
    ),
    "sp_advertised_products": ReportPreset(
        "spAdvertisedProduct",
        ("advertiser",),
        (
            "date",
            "campaignId",
            "campaignName",
            "adGroupId",
            "adGroupName",
            "adId",
            "advertisedAsin",
            "advertisedSku",
            *_METRICS_1_7_14,
        ),
        key_columns=("adId",),
        description="One row per advertised product per day.",
    ),
    "sp_purchased_products": ReportPreset(
        "spPurchasedProduct",
        ("asin",),
        (
            "date",
            "campaignId",
            "campaignName",
            "adGroupId",
            "adGroupName",
            "keywordId",
            "keyword",
            "matchType",
            "advertisedAsin",
            "purchasedAsin",
            "unitsSoldOtherSku14d",
            "salesOtherSku14d",
            "purchases14d",
            "sales14d",
            "kindleEditionNormalizedPagesRead14d",
            "kindleEditionNormalizedPagesRoyalties14d",
        ),
        key_columns=("adGroupId", "keywordId", "matchType", "advertisedAsin", "purchasedAsin"),
        description="Products bought after an ad click that were not the advertised "
        "product (halo sales).",
    ),
    "sb_campaigns": ReportPreset(
        "sbCampaigns",
        ("campaign",),
        (
            "date",
            "campaignId",
            "campaignName",
            "campaignStatus",
            "campaignBudgetAmount",
            "campaignBudgetType",
            "campaignBudgetCurrencyCode",
            "topOfSearchImpressionShare",
            *_SB_METRICS,
            "detailPageViews",
            "newToBrandPurchases",
            "brandedSearches",
            "kindleEditionNormalizedPagesRead14d",
            "kindleEditionNormalizedPagesRoyalties14d",
        ),
        key_columns=("campaignId",),
        ad_product="SPONSORED_BRANDS",
        retention_days=_SB_RETENTION_DAYS,
        description="One row per Sponsored Brands campaign per day. Sales count any "
        "product of the brand, not only the ones in the ad.",
    ),
    "sb_targeting": ReportPreset(
        "sbTargeting",
        ("targeting",),
        (
            "date",
            "campaignId",
            "campaignName",
            "adGroupId",
            "adGroupName",
            "keywordId",
            "keywordText",
            "keywordType",
            "matchType",
            "targetingId",
            "targetingExpression",
            "targetingText",
            "targetingType",
            "keywordBid",
            "adKeywordStatus",
            "topOfSearchImpressionShare",
            *_SB_METRICS,
            "detailPageViews",
            "newToBrandPurchases",
            "brandedSearches",
        ),
        key_columns=("adGroupId", "targetingId"),
        ad_product="SPONSORED_BRANDS",
        retention_days=_SB_RETENTION_DAYS,
        description="One row per Sponsored Brands keyword or product target per day. "
        "keywordBid is the bid at pull time.",
    ),
    "sb_search_terms": ReportPreset(
        "sbSearchTerm",
        ("searchTerm",),
        (
            "date",
            "campaignId",
            "campaignName",
            "adGroupId",
            "adGroupName",
            "keywordId",
            "keywordText",
            "keywordType",
            "matchType",
            "searchTerm",
            *_SB_METRICS,
        ),
        retention_days=_SB_RETENTION_DAYS,
        key_columns=("adGroupId", "keywordId", "searchTerm"),
        ad_product="SPONSORED_BRANDS",
        description="One row per customer search term per Sponsored Brands target per day.",
    ),
    "sb_purchased_products": ReportPreset(
        "sbPurchasedProduct",
        ("purchasedAsin",),
        (
            "date",
            "campaignId",
            "campaignName",
            "adGroupId",
            "adGroupName",
            "attributionType",
            "purchasedAsin",
            "productName",
            "productCategory",
            "orders14d",
            "sales14d",
            "unitsSold14d",
            "newToBrandPurchases14d",
            "newToBrandSales14d",
            "newToBrandUnitsSold14d",
        ),
        key_columns=("campaignId", "adGroupId", "purchasedAsin", "attributionType"),
        ad_product="SPONSORED_BRANDS",
        retention_days=_SB_RETENTION_DAYS,
        description="Every product bought within 14 days of a Sponsored Brands click, "
        "which can be any product of the brand. attributionType says whether it was in "
        "the ad (Promoted) or not (Brand Halo).",
    ),
}


@dataclass
class ReportStatus:
    report_id: str
    status: str
    url: str | None = None
    url_expires_at: str | None = None
    file_size: int | None = None
    failure_reason: str | None = None
    raw: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_api(cls, data: dict[str, Any]) -> ReportStatus:
        return cls(
            report_id=str(data.get("reportId")),
            status=str(data.get("status")),
            url=data.get("url"),
            url_expires_at=data.get("urlExpiresAt"),
            file_size=data.get("fileSize"),
            failure_reason=data.get("failureReason"),
            raw=data,
        )


@dataclass
class ReportChunk:
    start: date
    end: date
    report_id: str
    rows: int = 0


@dataclass
class ReportResult:
    rows: list[dict[str, Any]]
    chunks: list[ReportChunk]
    configuration: dict[str, Any]
    start: date
    end: date

    def to_dataframe(self) -> Any:
        """The rows as a pandas DataFrame (``pip install amazon-ads-py[pandas]``)."""
        try:
            import pandas as pd
        except ImportError as exc:  # pragma: no cover - depends on environment
            raise ImportError("to_dataframe() needs pandas: pip install pandas") from exc
        return pd.DataFrame(self.rows)


def date_chunks(start: date, end: date, max_days: int = MAX_DAYS) -> list[tuple[date, date]]:
    """Split an inclusive date range into consecutive ranges of at most ``max_days``."""
    if end < start:
        raise ValueError(f"end {end} is before start {start}")
    chunks = []
    cursor = start
    while cursor <= end:
        chunk_end = min(cursor + timedelta(days=max_days - 1), end)
        chunks.append((cursor, chunk_end))
        cursor = chunk_end + timedelta(days=1)
    return chunks


_DUPLICATE = re.compile(r"duplicate of\s*:?\s*([0-9a-fA-F-]{8,})")


class Reports:
    def __init__(
        self,
        client: ProfileClient,
        *,
        sleep: Callable[[float], None] = time.sleep,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._client = client
        self._sleep = sleep
        self._monotonic = monotonic

    # --- the one-call path --------------------------------------------------------------

    def run(
        self,
        report: str | dict[str, Any],
        start: date,
        end: date,
        *,
        columns: Sequence[str] | None = None,
        filters: Iterable[dict[str, Any]] | None = None,
        time_unit: str = "DAILY",
        timeout: float = 3600.0,
        name: str | None = None,
    ) -> ReportResult:
        """Request, wait for and download a report over any date range.

        ``report`` is a preset name (see :data:`PRESETS`) or a full Amazon
        ``configuration`` dict. With ``time_unit="SUMMARY"`` rows are totals per chunk,
        so ranges over 31 days yield one total per chunk rather than one overall.
        """
        configuration = self.configuration(
            report, columns=columns, filters=filters, time_unit=time_unit
        )
        self._warn_retention(report, start)
        chunks = [
            ReportChunk(s, e, self.create(configuration, s, e, name=name))
            for s, e in date_chunks(start, end)
        ]
        rows: list[dict[str, Any]] = []
        deadline = self._monotonic() + timeout
        for chunk in chunks:
            remaining = max(deadline - self._monotonic(), 1.0)
            status = self.wait(chunk.report_id, timeout=remaining)
            chunk_rows = self.download(status)
            chunk.rows = len(chunk_rows)
            rows.extend(chunk_rows)
        return ReportResult(
            rows=rows, chunks=chunks, configuration=configuration, start=start, end=end
        )

    # --- building blocks ----------------------------------------------------------------

    @staticmethod
    def configuration(
        report: str | dict[str, Any],
        *,
        columns: Sequence[str] | None = None,
        filters: Iterable[dict[str, Any]] | None = None,
        time_unit: str = "DAILY",
    ) -> dict[str, Any]:
        if isinstance(report, dict):
            config = dict(report)
        else:
            try:
                preset = PRESETS[report]
            except KeyError:
                raise ValueError(
                    f"Unknown report preset {report!r}. Presets: {', '.join(sorted(PRESETS))}"
                ) from None
            config = {
                "adProduct": preset.ad_product,
                "reportTypeId": preset.report_type_id,
                "groupBy": list(preset.group_by),
                "columns": list(preset.columns),
                "format": "GZIP_JSON",
            }
        if columns is not None:
            config["columns"] = list(columns)
        config.setdefault("format", "GZIP_JSON")
        config["timeUnit"] = time_unit.upper()
        cols = config.get("columns") or []
        if config["timeUnit"] == "SUMMARY":
            # Amazon rejects the "date" column on summary reports.
            config["columns"] = [c for c in cols if c != "date"]
        elif "date" not in cols:
            config["columns"] = ["date", *cols]
        if filters:
            config["filters"] = list(filters)
        return config

    def create(
        self,
        configuration: dict[str, Any],
        start: date,
        end: date,
        *,
        name: str | None = None,
    ) -> str:
        """Queue a report and return its id. A duplicate of a report still being built
        returns that report's id instead of an error."""
        body = {
            "name": name or f"{configuration.get('reportTypeId')} {start}..{end}",
            "startDate": start.isoformat(),
            "endDate": end.isoformat(),
            "configuration": configuration,
        }
        try:
            response = self._client.request(
                "POST",
                "/reporting/reports",
                json=body,
                content_type=CREATE_TYPE,
                accept=CREATE_ACCEPT,
            )
        except ApiError as exc:
            if exc.status_code == 425:
                match = _DUPLICATE.search(str(exc.body) + " " + str(exc))
                if match:
                    log.info("Report already queued as %s; reusing it", match.group(1))
                    return match.group(1)
            raise
        return str(response.json()["reportId"])

    def status(self, report_id: str) -> ReportStatus:
        response = self._client.request("GET", f"/reporting/reports/{report_id}", accept=GET_ACCEPT)
        return ReportStatus.from_api(response.json())

    def wait(
        self, report_id: str, *, timeout: float = 3600.0, poll: float = 5.0, max_poll: float = 60.0
    ) -> ReportStatus:
        """Poll until the report completes. Raises :class:`ReportFailedError` on
        ``FAILED`` or timeout. Polling backs off from ``poll`` to ``max_poll`` seconds."""
        deadline = self._monotonic() + timeout
        interval = poll
        while True:
            status = self.status(report_id)
            if status.status == "COMPLETED":
                return status
            if status.status in {"FAILED", "FAILURE", "CANCELLED"}:
                raise ReportFailedError(
                    f"Report {report_id} failed: {status.failure_reason}",
                    report_id=report_id,
                    status=status.status,
                )
            if self._monotonic() + interval > deadline:
                raise ReportFailedError(
                    f"Report {report_id} still {status.status} after {timeout:.0f}s",
                    report_id=report_id,
                    status=status.status,
                )
            self._sleep(interval)
            interval = min(interval * 1.5, max_poll)

    def download(self, status: ReportStatus | str) -> list[dict[str, Any]]:
        """Download a completed report's rows. Accepts a status or a report id. The
        pre-signed URL expires (an hour by default), so a stale status is re-fetched."""
        if isinstance(status, str):
            status = self.status(status)
        if not status.url:
            raise ReportFailedError(
                f"Report {status.report_id} has no download URL (status {status.status})",
                report_id=status.report_id,
                status=status.status,
            )
        try:
            raw = self._client.account.transport.download(status.url)
        except ApiError as exc:
            # An expired pre-signed URL answers 400 or 403; a fresh status has a new one.
            if exc.status_code in {400, 403}:
                fresh = self.status(status.report_id).url or ""
                raw = self._client.account.transport.download(fresh)
            else:
                raise
        return parse_report_file(raw)

    def delete(self, report_id: str) -> None:
        """Cancel a pending report."""
        self._client.request("DELETE", f"/reporting/reports/{report_id}", accept=DELETE_ACCEPT)

    @staticmethod
    def _warn_retention(report: str | dict[str, Any], start: date) -> None:
        retention = PRESETS[report].retention_days if isinstance(report, str) else 95
        oldest = date.today() - timedelta(days=retention)
        if start < oldest:
            warnings.warn(
                f"Start {start} is more than {retention} days ago; Amazon keeps about "
                f"{retention} days of this report, so the earliest days may come back empty",
                stacklevel=3,
            )


def parse_report_file(raw: bytes) -> list[dict[str, Any]]:
    """Rows from a report file: gzipped (or plain) JSON array, or JSON lines."""
    data = gzip.decompress(raw) if raw[:2] == b"\x1f\x8b" else raw
    text = data.decode("utf-8").strip()
    if not text:
        return []
    if text.startswith("["):
        rows: list[dict[str, Any]] = json.loads(text)
        return rows
    return [json.loads(line) for line in text.splitlines() if line.strip()]
