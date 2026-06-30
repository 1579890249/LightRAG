"""SQL source loader for configurable KG mappings."""

from __future__ import annotations

import asyncio
import sqlite3
from contextlib import contextmanager
from typing import Any, Iterator
from urllib.parse import urlparse

from .config import MappingConfig


class ConfiguredSQLSource:
    """Load row dictionaries for every source in a mapping config."""

    def __init__(self, connection_url: str, config: MappingConfig) -> None:
        self.connection_url = connection_url
        self.config = config

    def load(self) -> dict[str, list[dict[str, Any]]]:
        rows_by_source: dict[str, list[dict[str, Any]]] = {}
        parsed = urlparse(self.connection_url)
        if parsed.scheme.lower() in {"postgresql", "postgres"}:
            return _load_postgres(self.connection_url, self.config)

        with _connect(self.connection_url) as connection:
            for source_name, source_config in self.config.sources.items():
                rows_by_source[source_name] = _fetch(
                    connection,
                    _query_for_source(source_name, source_config),
                )
        return rows_by_source


def _fetch(connection: Any, query: str) -> list[dict[str, Any]]:
    cursor = connection.execute(query)
    columns = [column[0] for column in cursor.description]
    rows = []
    for row in cursor.fetchall():
        rows.append(
            {
                column: value
                for column, value in zip(columns, row)
            }
        )
    return rows


def _query_for_source(source_name: str, source_config: dict[str, Any]) -> str:
    query = source_config.get("query")
    if query:
        return str(query)
    table_name = source_config.get("table") or source_name
    return f'SELECT * FROM "{table_name}"'


def _load_postgres(
    connection_url: str,
    config: MappingConfig,
) -> dict[str, list[dict[str, Any]]]:
    try:
        return _load_postgres_with_psycopg(connection_url, config)
    except RuntimeError as psycopg_error:
        try:
            return asyncio.run(_load_postgres_with_asyncpg(connection_url, config))
        except ImportError as asyncpg_error:
            raise psycopg_error from asyncpg_error


def _load_postgres_with_psycopg(
    connection_url: str,
    config: MappingConfig,
) -> dict[str, list[dict[str, Any]]]:
    try:
        import psycopg  # type: ignore
    except ImportError as exc:
        raise RuntimeError(
            "PostgreSQL KG mapping ingestion requires 'psycopg' or 'asyncpg'."
        ) from exc

    rows_by_source: dict[str, list[dict[str, Any]]] = {}
    with psycopg.connect(connection_url) as connection:
        for source_name, source_config in config.sources.items():
            rows_by_source[source_name] = _fetch(
                connection,
                _query_for_source(source_name, source_config),
            )
    return rows_by_source


async def _load_postgres_with_asyncpg(
    connection_url: str,
    config: MappingConfig,
) -> dict[str, list[dict[str, Any]]]:
    import asyncpg  # type: ignore

    connection = await asyncpg.connect(connection_url)
    try:
        rows_by_source: dict[str, list[dict[str, Any]]] = {}
        for source_name, source_config in config.sources.items():
            records = await connection.fetch(
                _query_for_source(source_name, source_config)
            )
            rows_by_source[source_name] = [
                {key: record[key] for key in record.keys()}
                for record in records
            ]
        return rows_by_source
    finally:
        await connection.close()


@contextmanager
def _connect(connection_url: str) -> Iterator[Any]:
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

    if scheme in {"mysql", "mariadb"}:
        try:
            import pymysql  # type: ignore
        except ImportError as exc:
            raise RuntimeError(
                "MySQL KG mapping ingestion requires the optional 'pymysql' package."
            ) from exc
        connection = pymysql.connect(
            host=parsed.hostname,
            port=parsed.port or 3306,
            user=parsed.username,
            password=parsed.password,
            database=parsed.path.lstrip("/"),
            cursorclass=pymysql.cursors.Cursor,
        )
        try:
            yield connection
        finally:
            connection.close()
        return

    raise ValueError(f"Unsupported KG mapping database URL scheme: {scheme}")
