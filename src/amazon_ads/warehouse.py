"""A local SQLite store that keeps report history past Amazon's retention window.

Amazon keeps 65 to 95 days of report data and keeps revising recent days: conversions
for up to about six weeks as late orders are attributed, impressions for a few days.
:meth:`Warehouse.sync` handles both. Each run re-pulls the trailing ``restate_days``
(42 by default) plus anything newer than the last sync, and **replaces** those days
wholesale, so a row Amazon later dropped disappears too. Older days are left alone and
survive after Amazon deletes them.

Rows are stored as JSON with their identifying columns, and a view per report
(``v_sp_placement`` and so on) exposes every column as a real SQL column, so any tool
that reads SQLite can query the data without this library::

    SELECT date, campaignName, placementClassification, SUM(cost)
    FROM v_sp_placement WHERE country_code = 'US' GROUP BY 1, 2, 3;
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Iterable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any

from amazon_ads.reports import PRESETS

if TYPE_CHECKING:
    from amazon_ads.client import ProfileClient

DEFAULT_RESTATE_DAYS = 42

SCHEMA = """
CREATE TABLE IF NOT EXISTS report_rows (
    profile_id   INTEGER NOT NULL,
    country_code TEXT    NOT NULL,
    report       TEXT    NOT NULL,
    date         TEXT    NOT NULL,
    row_key      TEXT    NOT NULL,
    data         TEXT    NOT NULL,
    pulled_at    TEXT    NOT NULL,
    PRIMARY KEY (profile_id, report, date, row_key)
);
CREATE INDEX IF NOT EXISTS report_rows_by_date ON report_rows (report, date);

CREATE TABLE IF NOT EXISTS sync_runs (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    profile_id   INTEGER NOT NULL,
    country_code TEXT    NOT NULL,
    report       TEXT    NOT NULL,
    start_date   TEXT    NOT NULL,
    end_date     TEXT    NOT NULL,
    rows         INTEGER NOT NULL,
    report_ids   TEXT    NOT NULL,
    pulled_at    TEXT    NOT NULL
);
"""


@dataclass
class SyncOutcome:
    country_code: str
    report: str
    start: date
    end: date
    rows: int
    report_ids: list[str]

    def __str__(self) -> str:
        return f"{self.country_code} {self.report}: {self.rows} rows for {self.start}..{self.end}"


class Warehouse:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path).expanduser()
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(self.path)
        self._db.row_factory = sqlite3.Row
        self._db.executescript(SCHEMA)
        self._ensure_views()

    def close(self) -> None:
        self._db.close()

    def __enter__(self) -> Warehouse:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # --- syncing ------------------------------------------------------------------------

    def sync(
        self,
        client: ProfileClient,
        reports: Iterable[str] = ("sp_placement", "sp_targeting", "sp_search_terms"),
        *,
        restate_days: int = DEFAULT_RESTATE_DAYS,
        start: date | None = None,
        end: date | None = None,
    ) -> list[SyncOutcome]:
        """Bring the store up to date for one profile.

        Without ``start`` each report resumes ``restate_days`` before its newest stored
        day, or reaches back as far as Amazon retains on the first run. ``end`` defaults
        to yesterday, the last complete day.
        """
        outcomes = []
        last_day = end or (date.today() - timedelta(days=1))
        for report in reports:
            preset = PRESETS[report]
            oldest_available = date.today() - timedelta(days=preset.retention_days - 1)
            if start is not None:
                first_day = start
            else:
                newest = self.newest_date(client.profile_id, report)
                first_day = (
                    max(newest - timedelta(days=restate_days), oldest_available)
                    if newest
                    else oldest_available
                )
            if first_day > last_day:
                continue
            result = client.reports.run(report, first_day, last_day)
            report_ids = [c.report_id for c in result.chunks]
            self.replace(
                client.profile_id,
                client.country_code,
                report,
                first_day,
                last_day,
                result.rows,
                report_ids=report_ids,
            )
            outcomes.append(
                SyncOutcome(
                    client.country_code, report, first_day, last_day, len(result.rows), report_ids
                )
            )
        return outcomes

    def replace(
        self,
        profile_id: int,
        country_code: str,
        report: str,
        start: date,
        end: date,
        rows: Sequence[dict[str, Any]],
        *,
        report_ids: Sequence[str] = (),
    ) -> None:
        """Replace every stored row of ``report`` dated ``start``..``end`` with ``rows``,
        in one transaction."""
        pulled_at = datetime.now(UTC).isoformat(timespec="seconds")
        key_columns = _key_columns(report, rows)
        # Two rows sharing a key on the same day must both survive, so repeats get a
        # counter suffix instead of overwriting each other.
        seen: dict[tuple[str, str], int] = {}
        keyed: list[tuple[str, str, dict[str, Any]]] = []
        for row in rows:
            day = str(row.get("date") or start.isoformat())
            key = _row_key(row, key_columns)
            count = seen.get((day, key), 0)
            seen[(day, key)] = count + 1
            keyed.append((day, f"{key}-{count}" if count else key, row))
        with self._transaction() as db:
            db.execute(
                "DELETE FROM report_rows WHERE profile_id = ? AND report = ? "
                "AND date BETWEEN ? AND ?",
                (profile_id, report, start.isoformat(), end.isoformat()),
            )
            db.executemany(
                "INSERT OR REPLACE INTO report_rows "
                "(profile_id, country_code, report, date, row_key, data, pulled_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                [
                    (
                        profile_id,
                        country_code,
                        report,
                        day,
                        key,
                        json.dumps(row, separators=(",", ":"), sort_keys=True),
                        pulled_at,
                    )
                    for day, key, row in keyed
                ],
            )
            db.execute(
                "INSERT INTO sync_runs (profile_id, country_code, report, start_date, end_date,"
                " rows, report_ids, pulled_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    profile_id,
                    country_code,
                    report,
                    start.isoformat(),
                    end.isoformat(),
                    len(rows),
                    json.dumps(list(report_ids)),
                    pulled_at,
                ),
            )

    # --- reading ------------------------------------------------------------------------

    def newest_date(self, profile_id: int, report: str) -> date | None:
        row = self._db.execute(
            "SELECT MAX(end_date) FROM sync_runs WHERE profile_id = ? AND report = ?",
            (profile_id, report),
        ).fetchone()
        return date.fromisoformat(row[0]) if row and row[0] else None

    def rows(
        self,
        report: str,
        *,
        country_code: str | None = None,
        profile_id: int | None = None,
        start: date | None = None,
        end: date | None = None,
    ) -> Iterator[dict[str, Any]]:
        """Stored rows as dicts, oldest first, each with ``country_code`` added."""
        sql = "SELECT country_code, data FROM report_rows WHERE report = ?"
        params: list[Any] = [report]
        if country_code:
            sql += " AND country_code = ?"
            params.append(country_code.upper())
        if profile_id:
            sql += " AND profile_id = ?"
            params.append(profile_id)
        if start:
            sql += " AND date >= ?"
            params.append(start.isoformat())
        if end:
            sql += " AND date <= ?"
            params.append(end.isoformat())
        sql += " ORDER BY date, row_key"
        for record in self._db.execute(sql, params):
            row = json.loads(record["data"])
            row["country_code"] = record["country_code"]
            yield row

    def query(self, sql: str, params: Sequence[Any] = ()) -> list[dict[str, Any]]:
        """Run a read-only SQL query and return rows as dicts."""
        if not sql.lstrip().upper().startswith(("SELECT", "WITH")):
            raise ValueError("query() only runs SELECT statements")
        return [dict(r) for r in self._db.execute(sql, params)]

    def coverage(self) -> list[dict[str, Any]]:
        """Per market and report: first and last stored day, row count, last pull."""
        return self.query(
            "SELECT country_code, report, MIN(date) AS first_day, MAX(date) AS last_day, "
            "COUNT(*) AS rows, MAX(pulled_at) AS last_pulled FROM report_rows "
            "GROUP BY country_code, report ORDER BY country_code, report"
        )

    # --- internals ----------------------------------------------------------------------

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        try:
            yield self._db
            self._db.commit()
        except BaseException:
            self._db.rollback()
            raise

    def _ensure_views(self) -> None:
        for name, preset in PRESETS.items():
            columns = ",\n    ".join(
                f"json_extract(data, '$.{c}') AS \"{c}\"" for c in preset.columns if c != "date"
            )
            self._db.execute(f"DROP VIEW IF EXISTS v_{name}")
            self._db.execute(
                f"CREATE VIEW v_{name} AS SELECT profile_id, country_code, date, pulled_at,\n"
                f"    {columns}\nFROM report_rows WHERE report = '{name}'"
            )
        self._db.commit()


def _key_columns(report: str, rows: Sequence[dict[str, Any]]) -> tuple[str, ...]:
    preset = PRESETS.get(report)
    if preset and preset.key_columns:
        return preset.key_columns
    # Unknown report: every non-numeric column identifies the row.
    sample = rows[0] if rows else {}
    return tuple(sorted(k for k, v in sample.items() if not isinstance(v, int | float)))


def _row_key(row: dict[str, Any], key_columns: Sequence[str]) -> str:
    material = json.dumps([row.get(c) for c in key_columns], separators=(",", ":"), default=str)
    return hashlib.sha1(material.encode()).hexdigest()[:20]
