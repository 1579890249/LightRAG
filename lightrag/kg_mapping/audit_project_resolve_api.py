"""Audit project entity resolution API.

The resolver maps user-mentioned project names, short names, and aliases to
authoritative project IDs in the audit PostgreSQL database. It is intentionally
not part of KG mappings; it is a query helper for Dify/workflow orchestration.
"""

from __future__ import annotations

import asyncio
import re
import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from difflib import SequenceMatcher
from pathlib import Path
from typing import Any, Iterator
from urllib.parse import urlparse

import yaml
from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from lightrag.kg_mapping.audit_rule_api import DEFAULT_AUDIT_DB_URL

DEFAULT_PROJECT_RESOLVE_CONFIG = "configs/audit/project_resolve.yaml"


class ProjectResolveRequest(BaseModel):
    query: str = Field(min_length=1)
    limit: int = Field(default=5, ge=1, le=20)
    auto_resolve_score: float = Field(default=0.85, ge=0, le=1)
    auto_resolve_gap: float = Field(default=0.15, ge=0, le=1)


@dataclass(frozen=True)
class ProjectCandidate:
    project_id: str
    project_name: str | None
    tender_org: str | None
    status: str | None
    bid_time: str | None
    matched_alias: str | None
    match_source: str
    score: float
    matched_tokens: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "project_id": self.project_id,
            "project_name": self.project_name,
            "tender_org": self.tender_org,
            "status": self.status,
            "bid_time": self.bid_time,
            "matched_alias": self.matched_alias,
            "match_source": self.match_source,
            "score": round(self.score, 4),
            "matched_tokens": list(self.matched_tokens),
        }


def create_audit_project_resolve_router(
    *,
    connection_url: str = DEFAULT_AUDIT_DB_URL,
    config_path: str | Path = DEFAULT_PROJECT_RESOLVE_CONFIG,
    auth_dependency=None,
) -> APIRouter:
    """Create the audit project entity resolution router."""

    router = APIRouter(tags=["audit-project-resolve"])
    dependencies = [Depends(auth_dependency)] if auth_dependency is not None else []
    stop_words = _load_stop_words(config_path)

    @router.post("/audit/projects/resolve", dependencies=dependencies)
    async def resolve_project(payload: ProjectResolveRequest):
        result = await asyncio.to_thread(
            _resolve_project,
            connection_url,
            payload,
            stop_words,
        )
        return result

    return router


def _resolve_project(
    connection_url: str,
    payload: ProjectResolveRequest,
    stop_words: set[str] | None = None,
) -> dict[str, Any]:
    rows = _load_project_rows(connection_url)
    tokens = _tokenize(payload.query, stop_words or set())
    candidates = _score_projects(payload.query, tokens, rows)[: payload.limit]

    resolved = False
    entity: dict[str, Any] | None = None
    if candidates:
        best = candidates[0]
        second_score = candidates[1].score if len(candidates) > 1 else 0.0
        if (
            best.score >= payload.auto_resolve_score
            and best.score - second_score >= payload.auto_resolve_gap
        ):
            resolved = True
            entity = best.to_dict()

    return {
        "code": 200,
        "msg": "解析成功" if resolved else "找到多个相似项目，请确认要审查哪一个",
        "resolved": resolved,
        "entity_type": "project",
        "query": payload.query,
        "tokens": tokens,
        "entity": entity,
        "candidates": [candidate.to_dict() for candidate in candidates],
    }


def _load_project_rows(connection_url: str) -> list[dict[str, Any]]:
    parsed = urlparse(connection_url)
    if parsed.scheme.lower() in {"postgresql", "postgres"}:
        return asyncio.run(_load_project_rows_postgres_asyncpg(connection_url))

    with _connect(connection_url) as connection:
        return _fetch_all(
            connection,
            (
                "SELECT "
                "p.project_id, p.project_name, p.tender_org, p.status, "
                "CAST(p.bid_time AS TEXT) AS bid_time, "
                "pa.alias_name, pa.alias_type "
                "FROM project p "
                "LEFT JOIN project_alias pa ON pa.project_id = p.project_id"
            ),
        )


async def _load_project_rows_postgres_asyncpg(connection_url: str) -> list[dict[str, Any]]:
    connection = await _connect_asyncpg(connection_url)
    try:
        rows = await connection.fetch(
            (
                "SELECT "
                "p.project_id, p.project_name, p.tender_org, p.status, "
                "p.bid_time::text AS bid_time, "
                "pa.alias_name, pa.alias_type "
                "FROM project p "
                "LEFT JOIN project_alias pa ON pa.project_id = p.project_id"
            )
        )
        return [_record_to_dict(row) for row in rows]
    finally:
        await connection.close()


def _score_projects(
    query: str,
    tokens: list[str],
    rows: list[dict[str, Any]],
) -> list[ProjectCandidate]:
    by_project: dict[str, ProjectCandidate] = {}
    normalized_query = _normalize(query)

    for row in rows:
        project_id = _stringify(row.get("project_id"))
        if not project_id:
            continue

        project_name = _stringify(row.get("project_name"))
        alias_name = _stringify(row.get("alias_name"))
        names = [
            ("project_id", project_id),
            ("project_name", project_name),
            ("alias", alias_name),
        ]

        best_score = 0.0
        best_source = "project_name"
        best_alias: str | None = None
        best_tokens: tuple[str, ...] = ()
        for source, candidate_text in names:
            if not candidate_text:
                continue
            score, matched_tokens = _score_text(normalized_query, tokens, candidate_text)
            if source == "project_id" and _normalize(candidate_text) == normalized_query:
                score = 1.0
            elif source == "alias" and _normalize(candidate_text) in normalized_query:
                score = max(score, 0.92)
            elif source == "project_name" and _normalize(candidate_text) in normalized_query:
                score = max(score, 0.9)

            if score > best_score:
                best_score = score
                best_source = source
                best_alias = candidate_text if source == "alias" else None
                best_tokens = tuple(matched_tokens)

        if best_score <= 0:
            continue

        candidate = ProjectCandidate(
            project_id=project_id,
            project_name=project_name or None,
            tender_org=_stringify(row.get("tender_org")) or None,
            status=_stringify(row.get("status")) or None,
            bid_time=_stringify(row.get("bid_time")) or None,
            matched_alias=best_alias,
            match_source=best_source,
            score=min(best_score, 1.0),
            matched_tokens=best_tokens,
        )
        previous = by_project.get(project_id)
        if previous is None or candidate.score > previous.score:
            by_project[project_id] = candidate

    return sorted(
        by_project.values(),
        key=lambda item: (item.score, len(item.matched_tokens), item.project_id),
        reverse=True,
    )


def _score_text(
    normalized_query: str,
    tokens: list[str],
    candidate_text: str,
) -> tuple[float, list[str]]:
    normalized_candidate = _normalize(candidate_text)
    if not normalized_candidate:
        return 0.0, []

    if normalized_candidate == normalized_query:
        return 1.0, tokens
    if normalized_candidate in normalized_query or normalized_query in normalized_candidate:
        return 0.9, [token for token in tokens if token in normalized_candidate]

    matched_tokens = [token for token in tokens if token and token in normalized_candidate]
    token_score = 0.0
    if tokens:
        token_score = len(matched_tokens) / len(tokens)

    similarity = SequenceMatcher(None, normalized_query, normalized_candidate).ratio()
    score = max(token_score * 0.82, similarity * 0.72)
    if len(matched_tokens) >= 2:
        score += 0.08
    elif len(matched_tokens) == 1:
        score += 0.03
    return min(score, 1.0), matched_tokens


def _tokenize(text: str, stop_words: set[str]) -> list[str]:
    normalized = _normalize(text)
    if not normalized:
        return []

    tokens: list[str] = []
    try:
        import jieba  # type: ignore
    except ImportError:
        tokens = _fallback_tokenize(normalized)
    else:
        tokens = [token.strip() for token in jieba.lcut(text) if token.strip()]
        tokens.extend(re.findall(r"[a-z0-9]+", normalized))

    cleaned: list[str] = []
    seen: set[str] = set()
    for token in tokens:
        normalized_token = _normalize(token)
        if (
            not normalized_token
            or normalized_token in stop_words
            or len(normalized_token) < 2
            or (len(normalized_token) > 8 and not normalized_token.isascii())
            or normalized_token in seen
        ):
            continue
        cleaned.append(normalized_token)
        seen.add(normalized_token)
    return cleaned


def _fallback_tokenize(normalized: str) -> list[str]:
    ascii_tokens = re.findall(r"[a-z0-9]+", normalized)
    chinese_parts = re.findall(r"[\u4e00-\u9fff]+", normalized)
    tokens = ascii_tokens[:]
    for part in chinese_parts:
        tokens.append(part)
        if len(part) >= 4:
            tokens.extend(part[index : index + 2] for index in range(len(part) - 1))
            tokens.extend(part[index : index + 3] for index in range(len(part) - 2))
    return tokens


def _normalize(value: str) -> str:
    return re.sub(r"[\s\-_（）()《》【】\\[\\]，。,.、:：;；!?！？]+", "", value).lower()


def _load_stop_words(config_path: str | Path) -> set[str]:
    path = Path(config_path)
    if not path.exists():
        return set()

    with path.open("r", encoding="utf-8") as file:
        data = yaml.safe_load(file) or {}
    values = data.get("stop_words", [])
    if not isinstance(values, list):
        raise ValueError(f"stop_words must be a list in {path}")

    stop_words = {
        _normalize(str(value))
        for value in values
        if str(value).strip()
    }
    return stop_words


def _stringify(value: Any) -> str:
    return "" if value is None else str(value)


async def _connect_asyncpg(connection_url: str) -> Any:
    import asyncpg  # type: ignore

    return await asyncpg.connect(connection_url)


def _record_to_dict(record: Any) -> dict[str, Any]:
    return {key: record[key] for key in record.keys()}


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

    raise ValueError(f"Unsupported audit project resolve database URL scheme: {scheme}")


def _connect_postgres(connection_url: str) -> Any:
    try:
        import psycopg  # type: ignore
    except ImportError as exc:
        raise RuntimeError(
            "PostgreSQL audit project resolve API requires the optional "
            "'psycopg' package."
        ) from exc
    return psycopg.connect(connection_url)


def _fetch_all(
    connection: Any,
    query: str,
    params: tuple[Any, ...] = (),
) -> list[dict[str, Any]]:
    cursor = connection.cursor()
    cursor.execute(_adapt_placeholders(connection, query), params)
    columns = [column[0] for column in cursor.description]
    return [dict(zip(columns, row)) for row in cursor.fetchall()]


def _adapt_placeholders(connection: Any, query: str) -> str:
    if connection.__class__.__module__.startswith("sqlite3"):
        return query
    return query.replace("?", "%s")
