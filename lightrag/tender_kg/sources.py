"""Database and document sources for tender KG ingestion."""

from __future__ import annotations

import csv
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator
from urllib.parse import urlparse

from .schema import (
    AwardEvent,
    BidSubmission,
    EvaluationEvent,
    Organization,
    Person,
    Project,
    TenderDocument,
    TenderEvent,
    TenderKGDataset,
)


@dataclass(frozen=True)
class TenderKGQueries:
    projects: str | None = None
    organizations: str | None = None
    people: str | None = None
    tender_events: str | None = None
    bid_submissions: str | None = None
    evaluation_events: str | None = None
    award_events: str | None = None


class DatabaseTenderSource:
    """Loads tender KG records from SQL queries.

    The source is intentionally query-driven. Existing tender systems rarely
    share the same table names, so callers provide SQL that aliases output
    columns to the dataclass field names.
    """

    def __init__(self, connection_url: str, queries: TenderKGQueries) -> None:
        self.connection_url = connection_url
        self.queries = queries

    def load(self) -> TenderKGDataset:
        with _connect(self.connection_url) as connection:
            return TenderKGDataset(
                projects=[
                    Project(**row)
                    for row in self._fetch(connection, self.queries.projects)
                ],
                organizations=[
                    Organization(
                        **_normalize_list_fields(row, {"aliases"})
                    )
                    for row in self._fetch(connection, self.queries.organizations)
                ],
                people=[
                    Person(**_normalize_list_fields(row, {"aliases"}))
                    for row in self._fetch(connection, self.queries.people)
                ],
                tender_events=[
                    TenderEvent(**row)
                    for row in self._fetch(connection, self.queries.tender_events)
                ],
                bid_submissions=[
                    BidSubmission(
                        **_normalize_list_fields(
                            row,
                            {"contact_person_ids"},
                        )
                    )
                    for row in self._fetch(connection, self.queries.bid_submissions)
                ],
                evaluation_events=[
                    EvaluationEvent(
                        **_normalize_list_fields(
                            row,
                            {"reviewer_person_ids", "bid_submission_ids"},
                        )
                    )
                    for row in self._fetch(connection, self.queries.evaluation_events)
                ],
                award_events=[
                    AwardEvent(**row)
                    for row in self._fetch(connection, self.queries.award_events)
                ],
            )

    def _fetch(self, connection: Any, query: str | None) -> list[dict[str, Any]]:
        if not query:
            return []
        cursor = connection.execute(query)
        columns = [column[0] for column in cursor.description]
        return [
            {
                column: value
                for column, value in zip(columns, row)
                if value is not None
            }
            for row in cursor.fetchall()
        ]


class DocumentDirectorySource:
    """Loads real text documents from a directory as tender KG documents."""

    def __init__(
        self,
        root_dir: str | Path,
        *,
        patterns: tuple[str, ...] = ("*.txt", "*.md"),
        encoding: str = "utf-8",
    ) -> None:
        self.root_dir = Path(root_dir)
        self.patterns = patterns
        self.encoding = encoding

    def load(self) -> TenderKGDataset:
        documents: list[TenderDocument] = []
        for path in self._iter_files():
            content = path.read_text(encoding=self.encoding)
            documents.append(
                TenderDocument(
                    id=path.stem,
                    title=path.stem,
                    content=content,
                    file_path=str(path),
                )
            )
        return TenderKGDataset(documents=documents)

    def _iter_files(self) -> Iterator[Path]:
        if not self.root_dir.exists():
            raise FileNotFoundError(f"Tender document directory not found: {self.root_dir}")
        for pattern in self.patterns:
            yield from sorted(
                path
                for path in self.root_dir.rglob(pattern)
                if path.is_file()
            )


@contextmanager
def _connect(connection_url: str):
    parsed = urlparse(connection_url)
    scheme = parsed.scheme.lower()
    if scheme == "sqlite":
        db_path = parsed.path
        if parsed.netloc:
            db_path = f"//{parsed.netloc}{parsed.path}"
        connection = sqlite3.connect(db_path)
        try:
            yield connection
        finally:
            connection.close()
        return

    if scheme in {"postgresql", "postgres"}:
        try:
            import psycopg  # type: ignore
        except ImportError as exc:
            raise RuntimeError(
                "PostgreSQL ingestion requires the optional 'psycopg' package."
            ) from exc
        with psycopg.connect(connection_url) as connection:
            yield connection
        return

    if scheme in {"mysql", "mariadb"}:
        try:
            import pymysql  # type: ignore
        except ImportError as exc:
            raise RuntimeError(
                "MySQL ingestion requires the optional 'pymysql' package."
            ) from exc
        connection = pymysql.connect(
            host=parsed.hostname,
            port=parsed.port or 3306,
            user=parsed.username,
            password=parsed.password,
            database=parsed.path.lstrip("/"),
        )
        try:
            yield connection
        finally:
            connection.close()
        return

    raise ValueError(f"Unsupported tender KG database URL scheme: {scheme}")


def _normalize_list_fields(
    row: dict[str, Any],
    field_names: set[str],
) -> dict[str, Any]:
    normalized = dict(row)
    for field_name in field_names:
        if field_name in normalized:
            normalized[field_name] = _parse_list(normalized[field_name])
    return normalized


def _parse_list(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, tuple):
        return [str(item).strip() for item in value if str(item).strip()]
    text = str(value).strip()
    if not text:
        return []
    reader = csv.reader([text])
    return [item.strip() for item in next(reader) if item.strip()]
