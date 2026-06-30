"""Audit-rule CRUD API helpers.

This module lives with the audit KG mapping extension code so customer-specific
business table APIs do not spread through the core LightRAG API package.
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


DEFAULT_AUDIT_DB_URL = "postgresql://rag:rag@postgres:5432/audit"
_AUDIT_RULE_COLUMNS = (
    "id",
    "rule_name",
    "rule_basis",
    "rule_status",
    "rule_type",
    "remark",
)


class AuditRulePayload(BaseModel):
    rule_name: str | None = None
    rule_basis: str | None = None
    rule_status: str | None = None
    rule_type: str | None = None
    remark: str | None = None


class AuditRuleUpdatePayload(BaseModel):
    id: int = Field(gt=0)
    rule_name: str | None = None
    rule_basis: str | None = None
    rule_status: str | None = None
    rule_type: str | None = None
    remark: str | None = None


def create_audit_rule_router(
    *,
    connection_url: str = DEFAULT_AUDIT_DB_URL,
    auth_dependency=None,
) -> APIRouter:
    """Create the audit_rule CRUD router."""

    router = APIRouter(tags=["audit-rule"])
    dependencies = [Depends(auth_dependency)] if auth_dependency is not None else []

    @router.get("/audit/rules/page", dependencies=dependencies)
    async def page_rules(
        pageNum: int = Query(1, ge=1),
        pageSize: int = Query(30, ge=1),
    ):
        result = await asyncio.to_thread(
            _page_rules,
            connection_url,
            pageNum,
            pageSize,
        )
        return {
            "total": result["total"],
            "rows": result["rows"],
            "code": 200,
            "msg": "查询成功",
        }

    @router.get("/audit/rules/detail", dependencies=dependencies)
    async def detail_rule(id: int = Query(..., gt=0)):
        rule = await asyncio.to_thread(_get_rule, connection_url, id)
        if rule is None:
            return _not_found()
        return {"data": rule, "code": 200, "msg": "查询成功"}

    @router.post("/audit/rules/add", dependencies=dependencies)
    async def add_rule(payload: AuditRulePayload):
        rule_id = await asyncio.to_thread(_add_rule, connection_url, payload)
        return {"code": 200, "msg": "新增成功", "id": rule_id}

    @router.post("/audit/rules/update", dependencies=dependencies)
    async def update_rule(payload: AuditRuleUpdatePayload):
        updated = await asyncio.to_thread(_update_rule, connection_url, payload)
        if not updated:
            return _not_found()
        return {"code": 200, "msg": "修改成功"}

    @router.get("/audit/rules/delete", dependencies=dependencies)
    async def delete_rule(id: int = Query(..., gt=0)):
        deleted = await asyncio.to_thread(_delete_rule, connection_url, id)
        if not deleted:
            return _not_found()
        return {"code": 200, "msg": "删除成功"}

    return router


def _not_found() -> JSONResponse:
    return JSONResponse(status_code=404, content={"code": 404, "msg": "规则不存在"})


def _page_rules(connection_url: str, page_num: int, page_size: int) -> dict[str, Any]:
    parsed = urlparse(connection_url)
    if parsed.scheme.lower() in {"postgresql", "postgres"}:
        return asyncio.run(_page_rules_postgres_asyncpg(connection_url, page_num, page_size))

    offset = (page_num - 1) * page_size
    with _connect(connection_url) as connection:
        total = _fetch_one_value(connection, "SELECT count(*) FROM audit_rule")
        rows = _fetch_all(
            connection,
            (
                "SELECT id, rule_name, rule_basis, rule_status, rule_type, remark "
                "FROM audit_rule ORDER BY id LIMIT ? OFFSET ?"
            ),
            (page_size, offset),
        )
    return {"total": total, "rows": rows}


def _get_rule(connection_url: str, rule_id: int) -> dict[str, Any] | None:
    parsed = urlparse(connection_url)
    if parsed.scheme.lower() in {"postgresql", "postgres"}:
        return asyncio.run(_get_rule_postgres_asyncpg(connection_url, rule_id))

    with _connect(connection_url) as connection:
        return _fetch_one(
            connection,
            (
                "SELECT id, rule_name, rule_basis, rule_status, rule_type, remark "
                "FROM audit_rule WHERE id = ?"
            ),
            (rule_id,),
        )


def _add_rule(connection_url: str, payload: AuditRulePayload) -> int:
    parsed = urlparse(connection_url)
    if parsed.scheme.lower() in {"postgresql", "postgres"}:
        return asyncio.run(_add_rule_postgres_asyncpg(connection_url, payload))

    with _connect(connection_url) as connection:
        cursor = _execute(
            connection,
            (
                "INSERT INTO audit_rule "
                "(rule_name, rule_basis, rule_status, rule_type, remark) "
                "VALUES (?, ?, ?, ?, ?)"
            ),
            _payload_values(payload),
        )
        connection.commit()
        return int(cursor.lastrowid)


def _update_rule(connection_url: str, payload: AuditRuleUpdatePayload) -> bool:
    parsed = urlparse(connection_url)
    if parsed.scheme.lower() in {"postgresql", "postgres"}:
        return asyncio.run(_update_rule_postgres_asyncpg(connection_url, payload))

    update_fields = [
        field_name
        for field_name in _AUDIT_RULE_COLUMNS
        if field_name != "id" and field_name in payload.model_fields_set
    ]
    if not update_fields:
        return _get_rule(connection_url, payload.id) is not None

    assignments = ", ".join(f"{field_name} = ?" for field_name in update_fields)
    params = tuple(getattr(payload, field_name) for field_name in update_fields) + (
        payload.id,
    )
    with _connect(connection_url) as connection:
        cursor = _execute(
            connection,
            f"UPDATE audit_rule SET {assignments} WHERE id = ?",
            params,
        )
        connection.commit()
        return _rowcount(cursor) > 0


def _delete_rule(connection_url: str, rule_id: int) -> bool:
    parsed = urlparse(connection_url)
    if parsed.scheme.lower() in {"postgresql", "postgres"}:
        return asyncio.run(_delete_rule_postgres_asyncpg(connection_url, rule_id))

    with _connect(connection_url) as connection:
        cursor = _execute(connection, "DELETE FROM audit_rule WHERE id = ?", (rule_id,))
        connection.commit()
        return _rowcount(cursor) > 0


def _payload_values(payload: AuditRulePayload) -> tuple[Any, ...]:
    return (
        payload.rule_name,
        payload.rule_basis,
        payload.rule_status,
        payload.rule_type,
        payload.remark,
    )


async def _page_rules_postgres_asyncpg(
    connection_url: str,
    page_num: int,
    page_size: int,
) -> dict[str, Any]:
    offset = (page_num - 1) * page_size
    connection = await _connect_asyncpg(connection_url)
    try:
        total = await connection.fetchval("SELECT count(*) FROM audit_rule")
        rows = await connection.fetch(
            (
                "SELECT id, rule_name, rule_basis, rule_status, rule_type, remark "
                "FROM audit_rule ORDER BY id LIMIT $1 OFFSET $2"
            ),
            page_size,
            offset,
        )
        return {
            "total": int(total or 0),
            "rows": [_record_to_dict(row) for row in rows],
        }
    finally:
        await connection.close()


async def _get_rule_postgres_asyncpg(
    connection_url: str,
    rule_id: int,
) -> dict[str, Any] | None:
    connection = await _connect_asyncpg(connection_url)
    try:
        row = await connection.fetchrow(
            (
                "SELECT id, rule_name, rule_basis, rule_status, rule_type, remark "
                "FROM audit_rule WHERE id = $1"
            ),
            rule_id,
        )
        return _record_to_dict(row) if row else None
    finally:
        await connection.close()


async def _add_rule_postgres_asyncpg(
    connection_url: str,
    payload: AuditRulePayload,
) -> int:
    connection = await _connect_asyncpg(connection_url)
    try:
        rule_id = await connection.fetchval(
            (
                "INSERT INTO audit_rule "
                "(rule_name, rule_basis, rule_status, rule_type, remark) "
                "VALUES ($1, $2, $3, $4, $5) RETURNING id"
            ),
            *_payload_values(payload),
        )
        return int(rule_id)
    finally:
        await connection.close()


async def _update_rule_postgres_asyncpg(
    connection_url: str,
    payload: AuditRuleUpdatePayload,
) -> bool:
    update_fields = [
        field_name
        for field_name in _AUDIT_RULE_COLUMNS
        if field_name != "id" and field_name in payload.model_fields_set
    ]
    if not update_fields:
        return await _get_rule_postgres_asyncpg(connection_url, payload.id) is not None

    assignments = ", ".join(
        f"{field_name} = ${index}" for index, field_name in enumerate(update_fields, 1)
    )
    id_placeholder = f"${len(update_fields) + 1}"
    params = [getattr(payload, field_name) for field_name in update_fields]
    params.append(payload.id)

    connection = await _connect_asyncpg(connection_url)
    try:
        status = await connection.execute(
            f"UPDATE audit_rule SET {assignments} WHERE id = {id_placeholder}",
            *params,
        )
        return _asyncpg_status_count(status) > 0
    finally:
        await connection.close()


async def _delete_rule_postgres_asyncpg(connection_url: str, rule_id: int) -> bool:
    connection = await _connect_asyncpg(connection_url)
    try:
        status = await connection.execute(
            "DELETE FROM audit_rule WHERE id = $1",
            rule_id,
        )
        return _asyncpg_status_count(status) > 0
    finally:
        await connection.close()


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

    raise ValueError(f"Unsupported audit-rule database URL scheme: {scheme}")


def _connect_postgres(connection_url: str) -> Any:
    try:
        import psycopg  # type: ignore
    except ImportError as exc:
        raise RuntimeError(
            "PostgreSQL audit-rule API requires the optional 'psycopg' package."
        ) from exc
    return psycopg.connect(connection_url)


def _fetch_all(connection: Any, query: str, params: tuple[Any, ...] = ()) -> list[dict[str, Any]]:
    cursor = _execute(connection, query, params)
    columns = [column[0] for column in cursor.description]
    return [dict(zip(columns, row)) for row in cursor.fetchall()]


def _fetch_one(connection: Any, query: str, params: tuple[Any, ...] = ()) -> dict[str, Any] | None:
    rows = _fetch_all(connection, query, params)
    return rows[0] if rows else None


def _fetch_one_value(connection: Any, query: str, params: tuple[Any, ...] = ()) -> int:
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
