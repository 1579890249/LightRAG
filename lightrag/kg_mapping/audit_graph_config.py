"""Audit graph-path rule configuration helpers.

Business users maintain natural-language rules in ``audit_rule``. This module
manages the hidden compiled configuration table used by deterministic graph
path queries.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import sqlite3
from contextlib import contextmanager
from typing import Any, Iterator
from urllib.parse import urlparse

from .config import MappingConfig


DEFAULT_GRAPH_CONFIG_STATUS = "active"
ACTIVE_RULE_STATUSES = ("1", "enabled", "active", "启用")

_BID_GRAPH_ENTITY_TYPES = [
    "Person",
    "Organization",
    "Project",
    "BidSubmission",
    "ShareholdingRecord",
]
_PERSON_GRAPH_RELATION_TYPES = [
    "PERSON_RELATED",
    "HOLDS_POSITION",
    "PROJECT_PERSON_ROLE",
    "PROJECT_ROLE_ORG",
    "PROJECT_EXPERT",
    "BID_PERSON_ROLE",
    "BID_ROLE_ORG",
    "BIDDER",
    "FOR_PROJECT",
    "TENDERED_BY",
    "SHAREHOLDING_TARGET",
    "NATURAL_PERSON_SHAREHOLDER",
    "ENTERPRISE_SHAREHOLDER",
]
_EQUITY_GRAPH_RELATION_TYPES = [
    "BIDDER",
    "FOR_PROJECT",
    "TENDERED_BY",
    "BID_PERSON_ROLE",
    "BID_ROLE_ORG",
    "SHAREHOLDING_TARGET",
    "NATURAL_PERSON_SHAREHOLDER",
    "ENTERPRISE_SHAREHOLDER",
]
_BIDDING_GRAPH_RELATION_TYPES = [
    "BIDDER",
    "FOR_PROJECT",
    "TENDERED_BY",
    "BID_PERSON_ROLE",
    "BID_ROLE_ORG",
    "PROJECT_PERSON_ROLE",
    "PROJECT_ROLE_ORG",
]
_BUILTIN_RULE_TYPE_TEMPLATES = {
    "招投标人际关系风险": {
        "allowed_entity_types": _BID_GRAPH_ENTITY_TYPES,
        "relation_types": _PERSON_GRAPH_RELATION_TYPES,
        "default_max_depth": 4,
    },
    "人际关系": {
        "allowed_entity_types": _BID_GRAPH_ENTITY_TYPES,
        "relation_types": _PERSON_GRAPH_RELATION_TYPES,
        "default_max_depth": 4,
    },
    "股权关系": {
        "allowed_entity_types": [
            "Person",
            "Organization",
            "Project",
            "BidSubmission",
            "ShareholdingRecord",
        ],
        "relation_types": _EQUITY_GRAPH_RELATION_TYPES,
        "default_max_depth": 4,
    },
    "地址关联": {
        "allowed_entity_types": _BID_GRAPH_ENTITY_TYPES,
        "relation_types": _PERSON_GRAPH_RELATION_TYPES,
        "default_max_depth": 4,
    },
    "投标行为": {
        "allowed_entity_types": [
            "Person",
            "Organization",
            "Project",
            "BidSubmission",
        ],
        "relation_types": _BIDDING_GRAPH_RELATION_TYPES,
        "default_max_depth": 4,
    },
}


class AuditGraphRuleConfigError(ValueError):
    """Raised when a compiled graph rule config is invalid."""


def ensure_audit_rule_graph_config_table(connection_url: str) -> None:
    """Create the hidden graph-rule config table when it is missing."""

    parsed = urlparse(connection_url)
    if parsed.scheme.lower() in {"postgresql", "postgres"}:
        asyncio.run(_ensure_table_postgres_asyncpg(connection_url))
        return

    with _connect(connection_url) as connection:
        _execute(
            connection,
            """
            CREATE TABLE IF NOT EXISTS audit_rule_graph_config (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                rule_id INTEGER NOT NULL,
                config TEXT NOT NULL,
                basis_hash TEXT,
                status TEXT NOT NULL DEFAULT 'active',
                created_at TEXT DEFAULT CURRENT_TIMESTAMP,
                updated_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
            """,
        )
        connection.commit()


def load_active_graph_configs_for_rule_type(
    connection_url: str,
    rule_type: str,
    mapping_config: MappingConfig,
) -> list[dict[str, Any]]:
    """Load active compiled graph configs for enabled rules of a rule type."""

    ensure_audit_rule_graph_config_table(connection_url)
    parsed = urlparse(connection_url)
    if parsed.scheme.lower() in {"postgresql", "postgres"}:
        rows = asyncio.run(
            _load_active_graph_configs_postgres_asyncpg(connection_url, rule_type)
        )
    else:
        with _connect(connection_url) as connection:
            rows = _fetch_all(
                connection,
                """
                SELECT
                    argc.id AS config_id,
                    argc.rule_id,
                    ar.rule_name,
                    ar.rule_basis,
                    ar.rule_type,
                    ar.rule_status,
                    argc.config,
                    argc.basis_hash,
                    argc.status
                FROM audit_rule_graph_config argc
                JOIN audit_rule ar ON ar.id = argc.rule_id
                WHERE ar.rule_type = ?
                  AND ar.rule_status IN (?, ?, ?, ?)
                  AND argc.status = ?
                ORDER BY argc.id
                """,
                (rule_type, *ACTIVE_RULE_STATUSES, DEFAULT_GRAPH_CONFIG_STATUS),
            )

    configs = []
    for row in rows:
        config = _parse_config(row["config"])
        validate_graph_config(config, mapping_config)
        normalized = dict(row)
        normalized["config"] = config
        configs.append(normalized)
    return configs


def build_builtin_graph_config_for_rule_type(
    rule_type: str,
    mapping_config: MappingConfig,
    default_max_depth: int | None = None,
) -> dict[str, Any]:
    """Build a hidden traversal config from a business rule type template."""

    template = _BUILTIN_RULE_TYPE_TEMPLATES.get(rule_type)
    if template is None:
        raise AuditGraphRuleConfigError(
            f"No builtin graph config template found for rule_type '{rule_type}'"
        )

    known_entity_types = set(mapping_config.entity_types)
    known_relation_types = {
        str(relation["relation_type"])
        for relation in mapping_config.relationships
        if relation.get("relation_type")
    }
    config = {
        "allowed_entity_types": [
            entity_type
            for entity_type in template["allowed_entity_types"]
            if entity_type in known_entity_types
        ],
        "relation_types": [
            relation_type
            for relation_type in template["relation_types"]
            if relation_type in known_relation_types
        ],
        "default_max_depth": default_max_depth or template["default_max_depth"],
    }

    if not config["allowed_entity_types"]:
        raise AuditGraphRuleConfigError(
            f"Builtin graph config for rule_type '{rule_type}' has no supported entity types"
        )
    if not config["relation_types"]:
        raise AuditGraphRuleConfigError(
            f"Builtin graph config for rule_type '{rule_type}' has no supported relation types"
        )
    return validate_graph_config(config, mapping_config)


def upsert_graph_config_for_rules(
    connection_url: str,
    *,
    rule_type: str,
    config: dict[str, Any],
    mapping_config: MappingConfig,
    rule_name: str | None = None,
    rule_id: int | None = None,
    basis_hash: str | None = None,
) -> list[dict[str, Any]]:
    """Replace active hidden graph configs for matching enabled audit rules."""

    ensure_audit_rule_graph_config_table(connection_url)
    validate_graph_config(config, mapping_config)
    effective_basis_hash = basis_hash or _config_hash(rule_type, config)
    parsed = urlparse(connection_url)
    if parsed.scheme.lower() in {"postgresql", "postgres"}:
        return asyncio.run(
            _upsert_graph_config_postgres_asyncpg(
                connection_url,
                rule_type=rule_type,
                rule_name=rule_name,
                rule_id=rule_id,
                config=config,
                basis_hash=effective_basis_hash,
            )
        )

    with _connect(connection_url) as connection:
        rules = _find_matching_rules(connection, rule_type, rule_name, rule_id)
        results = []
        config_json = json.dumps(config, ensure_ascii=False, sort_keys=True)
        for rule in rules:
            _execute(
                connection,
                """
                UPDATE audit_rule_graph_config
                SET status = 'inactive', updated_at = CURRENT_TIMESTAMP
                WHERE rule_id = ? AND status = ?
                """,
                (rule["id"], DEFAULT_GRAPH_CONFIG_STATUS),
            )
            cursor = _execute(
                connection,
                """
                INSERT INTO audit_rule_graph_config (
                    rule_id, config, basis_hash, status, created_at, updated_at
                ) VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                """,
                (
                    rule["id"],
                    config_json,
                    effective_basis_hash,
                    DEFAULT_GRAPH_CONFIG_STATUS,
                ),
            )
            results.append(
                {
                    "config_id": cursor.lastrowid,
                    "rule_id": rule["id"],
                    "rule_name": rule["rule_name"],
                    "rule_basis": rule["rule_basis"],
                    "rule_type": rule["rule_type"],
                    "rule_status": rule["rule_status"],
                    "config": config,
                    "basis_hash": effective_basis_hash,
                    "status": DEFAULT_GRAPH_CONFIG_STATUS,
                }
            )
        connection.commit()
    return results


def validate_graph_config(
    config: dict[str, Any],
    mapping_config: MappingConfig,
) -> dict[str, Any]:
    """Validate a compiled graph path config against current KG mapping."""

    if not isinstance(config, dict):
        raise AuditGraphRuleConfigError("Graph rule config must be an object")

    allowed_entity_types = config.get("allowed_entity_types", [])
    relation_types = config.get("relation_types", [])
    default_max_depth = config.get("default_max_depth", 4)

    if not isinstance(allowed_entity_types, list) or not all(
        isinstance(item, str) and item.strip() for item in allowed_entity_types
    ):
        raise AuditGraphRuleConfigError(
            "Graph rule config field 'allowed_entity_types' must be a list of strings"
        )

    if not isinstance(relation_types, list) or not all(
        isinstance(item, str) and item.strip() for item in relation_types
    ):
        raise AuditGraphRuleConfigError(
            "Graph rule config field 'relation_types' must be a list of strings"
        )

    if not isinstance(default_max_depth, int) or not 1 <= default_max_depth <= 6:
        raise AuditGraphRuleConfigError(
            "Graph rule config field 'default_max_depth' must be an integer from 1 to 6"
        )

    known_entity_types = set(mapping_config.entity_types)
    unknown_entity_types = sorted(set(allowed_entity_types) - known_entity_types)
    if unknown_entity_types:
        raise AuditGraphRuleConfigError(
            "Unknown entity types in graph rule config: "
            + ", ".join(unknown_entity_types)
        )

    known_relation_types = {
        str(relation["relation_type"])
        for relation in mapping_config.relationships
        if relation.get("relation_type")
    }
    unknown_relation_types = sorted(set(relation_types) - known_relation_types)
    if unknown_relation_types:
        raise AuditGraphRuleConfigError(
            "Unknown relation types in graph rule config: "
            + ", ".join(unknown_relation_types)
        )

    return config


def _find_matching_rules(
    connection: Any,
    rule_type: str,
    rule_name: str | None,
    rule_id: int | None,
) -> list[dict[str, Any]]:
    where_parts = [
        "rule_type = ?",
        "rule_status IN (?, ?, ?, ?)",
    ]
    params: list[Any] = [rule_type, *ACTIVE_RULE_STATUSES]
    if rule_name:
        where_parts.append("rule_name = ?")
        params.append(rule_name)
    if rule_id is not None:
        where_parts.append("id = ?")
        params.append(rule_id)

    return _fetch_all(
        connection,
        (
            "SELECT id, rule_name, rule_basis, rule_status, rule_type, remark "
            "FROM audit_rule WHERE "
            + " AND ".join(where_parts)
            + " ORDER BY id"
        ),
        tuple(params),
    )


def _config_hash(rule_type: str, config: dict[str, Any]) -> str:
    payload = json.dumps(
        {"rule_type": rule_type, "config": config},
        ensure_ascii=False,
        sort_keys=True,
    )
    digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]
    return f"graph-config:{digest}"


def _parse_config(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        parsed = json.loads(value)
        if isinstance(parsed, dict):
            return parsed
    raise AuditGraphRuleConfigError("Graph rule config must be a JSON object")


async def _ensure_table_postgres_asyncpg(connection_url: str) -> None:
    connection = await _connect_asyncpg(connection_url)
    try:
        await connection.execute(
            """
            CREATE TABLE IF NOT EXISTS audit_rule_graph_config (
                id integer GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
                rule_id integer NOT NULL REFERENCES audit_rule(id) ON DELETE CASCADE,
                config jsonb NOT NULL,
                basis_hash text,
                status text NOT NULL DEFAULT 'active',
                created_at timestamp NOT NULL DEFAULT now(),
                updated_at timestamp NOT NULL DEFAULT now()
            )
            """
        )
    finally:
        await connection.close()


async def _load_active_graph_configs_postgres_asyncpg(
    connection_url: str,
    rule_type: str,
) -> list[dict[str, Any]]:
    connection = await _connect_asyncpg(connection_url)
    try:
        rows = await connection.fetch(
            """
            SELECT
                argc.id AS config_id,
                argc.rule_id,
                ar.rule_name,
                ar.rule_basis,
                ar.rule_type,
                ar.rule_status,
                argc.config,
                argc.basis_hash,
                argc.status
            FROM audit_rule_graph_config argc
            JOIN audit_rule ar ON ar.id = argc.rule_id
            WHERE ar.rule_type = $1
              AND ar.rule_status = ANY($3::text[])
              AND argc.status = $2
            ORDER BY argc.id
            """,
            rule_type,
            DEFAULT_GRAPH_CONFIG_STATUS,
            list(ACTIVE_RULE_STATUSES),
        )
        return [_record_to_dict(row) for row in rows]
    finally:
        await connection.close()


async def _upsert_graph_config_postgres_asyncpg(
    connection_url: str,
    *,
    rule_type: str,
    rule_name: str | None,
    rule_id: int | None,
    config: dict[str, Any],
    basis_hash: str,
) -> list[dict[str, Any]]:
    connection = await _connect_asyncpg(connection_url)
    try:
        rules = await connection.fetch(
            """
            SELECT id, rule_name, rule_basis, rule_status, rule_type, remark
            FROM audit_rule
            WHERE rule_type = $1
              AND rule_status = ANY($2::text[])
              AND ($3::text IS NULL OR rule_name = $3)
              AND ($4::integer IS NULL OR id = $4)
            ORDER BY id
            """,
            rule_type,
            list(ACTIVE_RULE_STATUSES),
            rule_name,
            rule_id,
        )
        results = []
        config_json = json.dumps(config, ensure_ascii=False, sort_keys=True)
        async with connection.transaction():
            for rule in rules:
                await connection.execute(
                    """
                    UPDATE audit_rule_graph_config
                    SET status = 'inactive', updated_at = now()
                    WHERE rule_id = $1 AND status = $2
                    """,
                    rule["id"],
                    DEFAULT_GRAPH_CONFIG_STATUS,
                )
                config_id = await connection.fetchval(
                    """
                    INSERT INTO audit_rule_graph_config (
                        rule_id, config, basis_hash, status
                    ) VALUES ($1, $2::jsonb, $3, $4)
                    RETURNING id
                    """,
                    rule["id"],
                    config_json,
                    basis_hash,
                    DEFAULT_GRAPH_CONFIG_STATUS,
                )
                results.append(
                    {
                        "config_id": config_id,
                        "rule_id": rule["id"],
                        "rule_name": rule["rule_name"],
                        "rule_basis": rule["rule_basis"],
                        "rule_type": rule["rule_type"],
                        "rule_status": rule["rule_status"],
                        "config": config,
                        "basis_hash": basis_hash,
                        "status": DEFAULT_GRAPH_CONFIG_STATUS,
                    }
                )
        return results
    finally:
        await connection.close()


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

    raise ValueError(f"Unsupported audit graph config database URL scheme: {scheme}")


def _connect_postgres(connection_url: str) -> Any:
    try:
        import psycopg  # type: ignore
    except ImportError as exc:
        raise RuntimeError(
            "PostgreSQL audit graph config API requires the optional 'psycopg' package."
        ) from exc
    return psycopg.connect(connection_url)


def _execute(connection: Any, query: str, params: tuple[Any, ...] = ()) -> Any:
    cursor = connection.cursor()
    cursor.execute(_adapt_placeholders(connection, query), params)
    return cursor


def _fetch_all(
    connection: Any,
    query: str,
    params: tuple[Any, ...] = (),
) -> list[dict[str, Any]]:
    cursor = _execute(connection, query, params)
    columns = [column[0] for column in cursor.description]
    return [dict(zip(columns, row)) for row in cursor.fetchall()]


def _adapt_placeholders(connection: Any, query: str) -> str:
    if connection.__class__.__module__.startswith("sqlite3"):
        return query
    return query.replace("?", "%s")
