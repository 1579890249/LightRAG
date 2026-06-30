"""Audit-result CRUD API helpers.

This module keeps customer-specific audit result table APIs beside the audit
KG mapping extension instead of spreading them through core LightRAG routes.
"""

from __future__ import annotations

import asyncio
import sqlite3
from contextlib import contextmanager
from typing import Any, Iterator
from urllib.parse import urlparse

from fastapi import APIRouter, Depends, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from lightrag.kg_mapping.audit_rule_api import DEFAULT_AUDIT_DB_URL

_AUDIT_RESULT_COLUMNS = (
    "sno",
    "project_id",
    "note",
    "status",
    "remark",
)


class AuditResultPayload(BaseModel):
    sno: str | None = Field(default=None, min_length=1)
    project_id: str | None = None
    note: str | None = None
    status: int | None = Field(default=0, ge=0, le=2)
    remark: str | None = None


class AuditResultUpdatePayload(BaseModel):
    sno: str = Field(min_length=1)
    project_id: str | None = None
    note: str | None = None
    status: int | None = Field(default=0, ge=0, le=2)
    remark: str | None = None


def create_audit_result_router(
    *,
    connection_url: str = DEFAULT_AUDIT_DB_URL,
    auth_dependency=None,
) -> APIRouter:
    """Create the audit_result CRUD router."""

    router = APIRouter(tags=["audit-result"])
    dependencies = [Depends(auth_dependency)] if auth_dependency is not None else []

    @router.get("/audit/results/page", dependencies=dependencies)
    async def page_results(
        pageNum: int = Query(1, ge=1),
        pageSize: int = Query(30, ge=1),
        projectId: str | None = Query(default=None),
        status: int | None = Query(default=None, ge=0, le=2),
    ):
        result = await asyncio.to_thread(
            _page_results,
            connection_url,
            pageNum,
            pageSize,
            projectId,
            status,
        )
        return {
            "total": result["total"],
            "rows": result["rows"],
            "code": 200,
            "msg": "查询成功",
        }

    @router.get("/audit/results/detail", dependencies=dependencies)
    async def detail_result(sno: str = Query(..., min_length=1)):
        result = await asyncio.to_thread(_get_result, connection_url, sno)
        if result is None:
            return _not_found()
        return {"data": result, "code": 200, "msg": "查询成功"}

    @router.post("/audit/results/add", dependencies=dependencies)
    async def add_result(payload: AuditResultPayload):
        sno = await asyncio.to_thread(_add_result, connection_url, payload)
        return {"code": 200, "msg": "新增成功", "sno": sno}

    @router.post("/audit/results/update", dependencies=dependencies)
    async def update_result(payload: AuditResultUpdatePayload):
        updated = await asyncio.to_thread(_update_result, connection_url, payload)
        if not updated:
            return _not_found()
        return {"code": 200, "msg": "修改成功"}

    @router.get("/audit/results/delete", dependencies=dependencies)
    async def delete_result(sno: str = Query(..., min_length=1)):
        deleted = await asyncio.to_thread(_delete_result, connection_url, sno)
        if not deleted:
            return _not_found()
        return {"code": 200, "msg": "删除成功"}

    return router


def _not_found() -> JSONResponse:
    return JSONResponse(status_code=404, content={"code": 404, "msg": "审计结果不存在"})


def _page_results(
    connection_url: str,
    page_num: int,
    page_size: int,
    project_id: str | None,
    status: int | None,
) -> dict[str, Any]:
    parsed = urlparse(connection_url)
    if parsed.scheme.lower() in {"postgresql", "postgres"}:
        return asyncio.run(
            _page_results_postgres_asyncpg(
                connection_url,
                page_num,
                page_size,
                project_id,
                status,
            )
        )

    offset = (page_num - 1) * page_size
    where_sql, params = _build_filters(project_id, status, placeholder="?")
    with _connect(connection_url) as connection:
        total = _fetch_one_value(
            connection,
            f"SELECT count(*) FROM audit_result{where_sql}",
            params,
        )
        rows = _fetch_all(
            connection,
            (
                "SELECT sno, project_id, note, status, remark "
                f"FROM audit_result{where_sql} "
                "ORDER BY sno DESC LIMIT ? OFFSET ?"
            ),
            params + (page_size, offset),
        )
    return {"total": total, "rows": rows}


def _get_result(connection_url: str, sno: str) -> dict[str, Any] | None:
    parsed = urlparse(connection_url)
    if parsed.scheme.lower() in {"postgresql", "postgres"}:
        return asyncio.run(_get_result_postgres_asyncpg(connection_url, sno))

    with _connect(connection_url) as connection:
        return _fetch_one(
            connection,
            (
                "SELECT sno, project_id, note, status, remark "
                "FROM audit_result WHERE sno = ?"
            ),
            (sno,),
        )


def _add_result(connection_url: str, payload: AuditResultPayload) -> str:
    parsed = urlparse(connection_url)
    if parsed.scheme.lower() in {"postgresql", "postgres"}:
        return asyncio.run(_add_result_postgres_asyncpg(connection_url, payload))

    with _connect(connection_url) as connection:
        if payload.sno is None:
            _execute(connection, "BEGIN IMMEDIATE")
        sno = payload.sno or _next_result_sno(connection)
        _execute(
            connection,
            (
                "INSERT INTO audit_result "
                "(sno, project_id, note, status, remark) "
                "VALUES (?, ?, ?, ?, ?)"
            ),
            _payload_values(sno, payload),
        )
        connection.commit()
        return sno


def _update_result(connection_url: str, payload: AuditResultUpdatePayload) -> bool:
    parsed = urlparse(connection_url)
    if parsed.scheme.lower() in {"postgresql", "postgres"}:
        return asyncio.run(_update_result_postgres_asyncpg(connection_url, payload))

    update_fields = [
        field_name
        for field_name in _AUDIT_RESULT_COLUMNS
        if field_name != "sno" and field_name in payload.model_fields_set
    ]
    if not update_fields:
        return _get_result(connection_url, payload.sno) is not None

    assignments = ", ".join(f"{field_name} = ?" for field_name in update_fields)
    params = tuple(getattr(payload, field_name) for field_name in update_fields) + (
        payload.sno,
    )
    with _connect(connection_url) as connection:
        cursor = _execute(
            connection,
            f"UPDATE audit_result SET {assignments} WHERE sno = ?",
            params,
        )
        connection.commit()
        return _rowcount(cursor) > 0


def _delete_result(connection_url: str, sno: str) -> bool:
    parsed = urlparse(connection_url)
    if parsed.scheme.lower() in {"postgresql", "postgres"}:
        return asyncio.run(_delete_result_postgres_asyncpg(connection_url, sno))

    with _connect(connection_url) as connection:
        cursor = _execute(connection, "DELETE FROM audit_result WHERE sno = ?", (sno,))
        connection.commit()
        return _rowcount(cursor) > 0


def _payload_values(sno: str, payload: AuditResultPayload) -> tuple[Any, ...]:
    return (
        sno,
        payload.project_id,
        payload.note,
        payload.status,
        payload.remark,
    )


def _next_result_sno(connection: Any) -> str:
    rows = _fetch_all(connection, "SELECT sno FROM audit_result")
    return _next_numeric_id((row["sno"] for row in rows), width=1)


def _next_numeric_id(values: Iterator[Any], *, width: int) -> str:
    max_value = 0
    max_width = width
    for value in values:
        text = str(value or "")
        if not text.isdigit():
            continue
        max_value = max(max_value, int(text))
        max_width = max(max_width, len(text))
    return f"{max_value + 1:0{max_width}d}"


def _build_filters(
    project_id: str | None,
    status: int | None,
    *,
    placeholder: str,
) -> tuple[str, tuple[Any, ...]]:
    clauses: list[str] = []
    params: list[Any] = []
    if project_id:
        clauses.append(f"project_id = {placeholder}")
        params.append(project_id)
    if status is not None:
        clauses.append(f"status = {placeholder}")
        params.append(status)
    if not clauses:
        return "", ()
    return " WHERE " + " AND ".join(clauses), tuple(params)


async def _page_results_postgres_asyncpg(
    connection_url: str,
    page_num: int,
    page_size: int,
    project_id: str | None,
    status: int | None,
) -> dict[str, Any]:
    offset = (page_num - 1) * page_size
    clauses: list[str] = []
    params: list[Any] = []
    if project_id:
        params.append(project_id)
        clauses.append(f"project_id = ${len(params)}")
    if status is not None:
        params.append(status)
        clauses.append(f"status = ${len(params)}")
    where_sql = " WHERE " + " AND ".join(clauses) if clauses else ""
    limit_placeholder = f"${len(params) + 1}"
    offset_placeholder = f"${len(params) + 2}"

    connection = await _connect_asyncpg(connection_url)
    try:
        total = await connection.fetchval(
            f"SELECT count(*) FROM audit_result{where_sql}",
            *params,
        )
        rows = await connection.fetch(
            (
                "SELECT sno, project_id, note, status, remark "
                f"FROM audit_result{where_sql} "
                f"ORDER BY sno DESC LIMIT {limit_placeholder} OFFSET {offset_placeholder}"
            ),
            *params,
            page_size,
            offset,
        )
        return {
            "total": int(total or 0),
            "rows": [_record_to_dict(row) for row in rows],
        }
    finally:
        await connection.close()


async def _get_result_postgres_asyncpg(
    connection_url: str,
    sno: str,
) -> dict[str, Any] | None:
    connection = await _connect_asyncpg(connection_url)
    try:
        row = await connection.fetchrow(
            (
                "SELECT sno, project_id, note, status, remark "
                "FROM audit_result WHERE sno = $1"
            ),
            sno,
        )
        return _record_to_dict(row) if row else None
    finally:
        await connection.close()


async def _add_result_postgres_asyncpg(
    connection_url: str,
    payload: AuditResultPayload,
) -> str:
    connection = await _connect_asyncpg(connection_url)
    try:
        async with connection.transaction():
            if payload.sno is None:
                await connection.execute("LOCK TABLE audit_result IN EXCLUSIVE MODE")
            sno = payload.sno or await _next_result_sno_postgres(connection)
            await connection.execute(
                (
                    "INSERT INTO audit_result "
                    "(sno, project_id, note, status, remark) "
                    "VALUES ($1, $2, $3, $4, $5)"
                ),
                *_payload_values(sno, payload),
            )
        return sno
    finally:
        await connection.close()


async def _update_result_postgres_asyncpg(
    connection_url: str,
    payload: AuditResultUpdatePayload,
) -> bool:
    update_fields = [
        field_name
        for field_name in _AUDIT_RESULT_COLUMNS
        if field_name != "sno" and field_name in payload.model_fields_set
    ]
    if not update_fields:
        return await _get_result_postgres_asyncpg(connection_url, payload.sno) is not None

    assignments = ", ".join(
        f"{field_name} = ${index}" for index, field_name in enumerate(update_fields, 1)
    )
    sno_placeholder = f"${len(update_fields) + 1}"
    params = [getattr(payload, field_name) for field_name in update_fields]
    params.append(payload.sno)

    connection = await _connect_asyncpg(connection_url)
    try:
        result = await connection.execute(
            f"UPDATE audit_result SET {assignments} WHERE sno = {sno_placeholder}",
            *params,
        )
        return _asyncpg_status_count(result) > 0
    finally:
        await connection.close()


async def _delete_result_postgres_asyncpg(connection_url: str, sno: str) -> bool:
    connection = await _connect_asyncpg(connection_url)
    try:
        result = await connection.execute(
            "DELETE FROM audit_result WHERE sno = $1",
            sno,
        )
        return _asyncpg_status_count(result) > 0
    finally:
        await connection.close()


async def _next_result_sno_postgres(connection: Any) -> str:
    rows = await connection.fetch("SELECT sno FROM audit_result")
    return _next_numeric_id((row["sno"] for row in rows), width=1)


async def _connect_asyncpg(connection_url: str) -> Any:
    import asyncpg  # type: ignore

    return await asyncpg.connect(connection_url)


def _record_to_dict(record: Any) -> dict[str, Any]:
    return {key: record[key] for key in record.keys()}


def _asyncpg_status_count(status: str) -> int:
    try:
        return int(status.rsplit(" ", 1)[1])
    except (IndexError, ValueError):
        return 0


@contextmanager
def _connect(connection_url: str) -> Iterator[Any]:
    parsed = urlparse(connection_url)
    scheme = parsed.scheme.lower()
    if scheme == "sqlite":
        db_path = parsed.path
        if parsed.netloc:
            db_path = f"//{parsed.netloc}{parsed.path}"
        connection = sqlite3.connect(db_path, check_same_thread=False)
        try:
            yield connection
        finally:
            connection.close()
        return

    if scheme in {"postgresql", "postgres"}:
        connection = _connect_postgres(connection_url)
        try:
            yield connection
        finally:
            connection.close()
        return

    raise ValueError(f"Unsupported audit-result database URL scheme: {scheme}")


def _connect_postgres(connection_url: str) -> Any:
    try:
        import psycopg  # type: ignore
    except ImportError as exc:
        raise RuntimeError(
            "PostgreSQL audit-result API requires the optional 'psycopg' package."
        ) from exc
    return psycopg.connect(connection_url)


def _fetch_all(
    connection: Any,
    query: str,
    params: tuple[Any, ...] = (),
) -> list[dict[str, Any]]:
    cursor = _execute(connection, query, params)
    columns = [column[0] for column in cursor.description]
    return [dict(zip(columns, row)) for row in cursor.fetchall()]


def _fetch_one(
    connection: Any,
    query: str,
    params: tuple[Any, ...] = (),
) -> dict[str, Any] | None:
    rows = _fetch_all(connection, query, params)
    return rows[0] if rows else None


def _fetch_one_value(
    connection: Any,
    query: str,
    params: tuple[Any, ...] = (),
) -> int:
    cursor = _execute(connection, query, params)
    row = cursor.fetchone()
    return int(row[0]) if row else 0


def _execute(connection: Any, query: str, params: tuple[Any, ...] = ()) -> Any:
    cursor = connection.cursor()
    cursor.execute(_adapt_placeholders(connection, query), params)
    return cursor


def _adapt_placeholders(connection: Any, query: str) -> str:
    if connection.__class__.__module__.startswith("sqlite3"):
        return query
    return query.replace("?", "%s")


def _rowcount(cursor: Any) -> int:
    value = getattr(cursor, "rowcount", 0)
    return int(value if value is not None else 0)
