"""Internal audit-specific routes."""

from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from lightrag.kg_mapping import (
    ConfigurableKGBuilder,
    ConfiguredSQLSource,
    diff_sync_records,
    load_mapping_config,
)
from lightrag.kg_mapping.apply import ApplyResult
from lightrag.kg_mapping.audit_project_resolve_api import (
    create_audit_project_resolve_router,
)
from lightrag.kg_mapping.audit_graph_path_api import (
    DEFAULT_AUDIT_MAPPING_PATH,
    create_audit_graph_path_router,
)
from lightrag.kg_mapping.audit_rule_api import (
    DEFAULT_AUDIT_DB_URL,
    create_audit_rule_router,
)
from lightrag.kg_mapping.audit_result_api import create_audit_result_router
from lightrag.kg_mapping.auto_generation_api import (
    create_kg_mapping_generation_router,
)
from lightrag.utils import logger


class AuditKGSyncRequest(BaseModel):
    mapping: str = Field(
        default="configs/kg_mappings/audit_customer_ys.yaml",
        description="Path to the KG mapping YAML file.",
    )
    connection_url: str = Field(
        default="postgresql://rag:rag@postgres:5432/audit",
        description="Business database connection URL.",
    )
    state: str | None = Field(
        default="/app/data/audit_kg_sync/audit_kg_state_server.json",
        description="Path to previous sync state JSON.",
    )
    output: str | None = Field(
        default="/app/data/audit_kg_sync/audit_kg_api_payload_server.json",
        description="Optional path for generated payload JSON.",
    )
    workspace: str | None = Field(
        default=None,
        description="Workspace to apply into. Defaults to current LightRAG workspace.",
    )
    apply: bool = Field(
        default=False,
        description="When true, insert the generated custom_kg into LightRAG.",
    )
    write_state: bool = Field(
        default=False,
        description="When true, write current sync records to state after success.",
    )


class AuditKGSourceDeleteRequest(BaseModel):
    source: str = Field(
        ...,
        min_length=1,
        description="KG mapping source/table name to delete from the graph.",
    )
    primary_key: str | None = Field(
        default=None,
        description=(
            "Optional source-row primary key. When omitted, all state records "
            "for the source are deleted from the graph."
        ),
    )
    database_name: str = Field(
        default="audit",
        description="Database name used to construct source_id when state is absent.",
    )
    state: str | None = Field(
        default="/app/data/audit_kg_sync/audit_kg_state_server.json",
        description="Path to KG sync state JSON used to find source-row provenance.",
    )
    workspace: str | None = Field(
        default=None,
        description="Workspace to delete from. Defaults to current LightRAG workspace.",
    )
    remove_from_state: bool = Field(
        default=False,
        description="When true, remove matched records from the sync state after deletion.",
    )


def create_audit_routes(
    rag,
    api_key: Optional[str] = None,
    auth_dependency=None,
    default_audit_db_url: str = DEFAULT_AUDIT_DB_URL,
    default_mapping_path: str = DEFAULT_AUDIT_MAPPING_PATH,
):
    router = APIRouter(tags=["audit"])
    if auth_dependency is None:
        from ..utils_api import get_combined_auth_dependency

        auth_dependency = get_combined_auth_dependency(api_key)

    router.include_router(
        create_audit_rule_router(
            connection_url=default_audit_db_url,
            auth_dependency=auth_dependency,
        )
    )
    router.include_router(
        create_audit_result_router(
            connection_url=default_audit_db_url,
            auth_dependency=auth_dependency,
        )
    )
    router.include_router(
        create_audit_project_resolve_router(
            connection_url=default_audit_db_url,
            auth_dependency=auth_dependency,
        )
    )
    router.include_router(
        create_audit_graph_path_router(
            rag,
            connection_url=default_audit_db_url,
            mapping_path=default_mapping_path,
            auth_dependency=auth_dependency,
        )
    )
    router.include_router(
        create_kg_mapping_generation_router(
            auth_dependency=auth_dependency,
            default_excluded_tables=[
                "audit_rule",
                "audit_rule_graph_config",
                "audit_result",
                "project_alias",
            ],
            default_excluded_table_patterns=[
                "audit_rule*_backup_*",
            ],
            sync_callback=lambda mapping, connection_url, workspace, apply, write_state, state: _run_audit_kg_sync(
                rag,
                AuditKGSyncRequest(
                    mapping=mapping,
                    connection_url=connection_url,
                    **({"state": state} if state is not None else {}),
                    workspace=workspace,
                    apply=apply,
                    write_state=write_state,
                ),
            ),
            llm_enhancer=lambda payload: _run_audit_kg_mapping_llm_enhancement(
                rag,
                payload,
            ),
        )
    )

    @router.post("/audit/kg-sync", dependencies=[Depends(auth_dependency)])
    async def audit_kg_sync(request: AuditKGSyncRequest):
        """Build and optionally apply the audit database KG payload."""

        try:
            return await _run_audit_kg_sync(rag, request)
        except HTTPException:
            raise
        except Exception as exc:
            logger.error(f"Audit KG sync failed: {exc}")
            raise HTTPException(
                status_code=500,
                detail=f"Audit KG sync failed: {exc}",
            ) from exc

    @router.post("/audit/kg-source/delete", dependencies=[Depends(auth_dependency)])
    async def audit_kg_source_delete(request: AuditKGSourceDeleteRequest):
        """Delete graph data generated from audit database source rows.

        This endpoint deletes only LightRAG custom-KG provenance for database
        rows. It does not delete or update business database tables.
        """

        try:
            return await _run_audit_kg_source_delete(rag, request)
        except HTTPException:
            raise
        except Exception as exc:
            logger.error(f"Audit KG source delete failed: {exc}")
            raise HTTPException(
                status_code=500,
                detail=f"Audit KG source delete failed: {exc}",
            ) from exc

    return router


async def _run_audit_kg_mapping_llm_enhancement(
    rag,
    payload: dict[str, Any],
) -> dict[str, Any]:
    role_kwargs = (
        dict(getattr(rag, "role_llm_kwargs", {}).get("query") or {})
        if getattr(rag, "role_llm_kwargs", None)
        else dict(getattr(rag, "llm_model_kwargs", {}) or {})
    )
    prompt = (
        "你是数据库知识图谱映射配置助手。请只基于输入 JSON 给出 kg_mapping 可读性增强建议。\n"
        "必须只输出一个 JSON 对象，不要输出 Markdown。\n"
        "允许输出字段：entity_labels, entities, relationships。\n"
        "只允许增强实体中文标签、entity_name_template、description_template、relation_type。\n"
        "禁止修改 source、SQL、primary_key、id_field、src/tgt endpoint。\n"
        "relation_type 必须是英文大写下划线风格。\n"
        "输入 JSON 如下：\n"
        f"{json.dumps(payload, ensure_ascii=False)}"
    )
    llm_func = getattr(rag, "role_llm_funcs", {}).get("query")
    if llm_func is None:
        raise RuntimeError("Query LLM function is not configured")
    response = await llm_func(
        prompt,
        stream=False,
        enable_cot=False,
        **role_kwargs,
    )
    if not isinstance(response, str):
        response = str(response)
    try:
        return _parse_llm_json_object(response)
    except ValueError as exc:
        logger.error(f"KG mapping LLM enhancement returned invalid JSON: {response[:500]}")
        raise RuntimeError(f"KG mapping LLM enhancement returned invalid JSON: {exc}") from exc


def _parse_llm_json_object(response: str) -> dict[str, Any]:
    text = response.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end <= start:
        raise ValueError("response does not contain a JSON object")
    data = json.loads(text[start : end + 1])
    if not isinstance(data, dict):
        raise ValueError("response JSON is not an object")
    return data


async def _run_audit_kg_sync(rag, request: AuditKGSyncRequest) -> dict[str, Any]:
    workspace = request.workspace or getattr(rag, "workspace", None)
    current_workspace = getattr(rag, "workspace", None)
    if request.apply and current_workspace and workspace != current_workspace:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Requested workspace '{workspace}' does not match current "
                f"LightRAG workspace '{current_workspace}'."
            ),
        )

    build_result = await asyncio.to_thread(_build_audit_kg_sync, request)
    mapping_config = build_result["mapping_config"]
    rows_by_source = build_result["rows_by_source"]
    result = build_result["result"]
    sync_diff = build_result["sync_diff"]

    summary: dict[str, Any] = {
        "schema_version": mapping_config.schema_version,
        "database_name": mapping_config.database_name,
        "workspace": workspace,
        "sources": {
            source_name: len(rows)
            for source_name, rows in rows_by_source.items()
        },
        "custom_kg": {
            "chunks": len(result.custom_kg["chunks"]),
            "entities": len(result.custom_kg["entities"]),
            "relationships": len(result.custom_kg["relationships"]),
        },
        "sync_diff": {
            "insert": len(sync_diff.to_insert),
            "update": len(sync_diff.to_update),
            "delete": len(sync_diff.to_delete),
            "unchanged": len(sync_diff.unchanged),
        },
        "applied": False,
    }

    if request.apply:
        sources_to_delete = sync_diff.to_delete + sync_diff.update_previous
        if sources_to_delete:
            summary["delete_result"] = await rag.adelete_custom_kg_sources(
                sources_to_delete
            )
            if summary["delete_result"].get("status") == "not_allowed":
                raise HTTPException(
                    status_code=409,
                    detail=summary["delete_result"].get(
                        "message",
                        "Custom KG source deletion not allowed",
                    ),
                )

        await rag.ainsert_custom_kg(result.custom_kg)
        apply_result = ApplyResult(
            inserted_chunks=len(result.custom_kg["chunks"]),
            inserted_entities=len(result.custom_kg["entities"]),
            inserted_relationships=len(result.custom_kg["relationships"]),
        )
        summary["applied"] = True
        summary["apply_result"] = {
            "inserted_chunks": apply_result.inserted_chunks,
            "inserted_entities": apply_result.inserted_entities,
            "inserted_relationships": apply_result.inserted_relationships,
        }
        if hasattr(rag, "aclear_cache"):
            await rag.aclear_cache()
            summary["cache_cleared"] = True

    if request.output:
        _write_json(
            request.output,
            {
                "custom_kg": result.custom_kg,
                "sync_records": result.sync_records,
                "sync_diff": {
                    "to_insert": sync_diff.to_insert,
                    "to_update": sync_diff.to_update,
                    "update_previous": sync_diff.update_previous,
                    "to_delete": sync_diff.to_delete,
                    "unchanged": sync_diff.unchanged,
                },
                "summary": summary,
            },
        )

    if request.write_state:
        if not request.state:
            raise HTTPException(
                status_code=400,
                detail="write_state requires state path",
            )
        _write_json(request.state, result.sync_records)

    return summary


async def _run_audit_kg_source_delete(
    rag,
    request: AuditKGSourceDeleteRequest,
) -> dict[str, Any]:
    workspace = request.workspace or getattr(rag, "workspace", None)
    current_workspace = getattr(rag, "workspace", None)
    if current_workspace and workspace != current_workspace:
        raise HTTPException(
            status_code=400,
            detail=(
                f"Requested workspace '{workspace}' does not match current "
                f"LightRAG workspace '{current_workspace}'."
            ),
        )

    state_records = _read_json_list(request.state) if request.state else []
    matched_records = _match_source_delete_records(
        state_records,
        request,
    )
    if not matched_records and request.primary_key:
        matched_records = [
            {
                "source": request.source,
                "primary_key": str(request.primary_key),
                "source_id": _source_id(
                    request.database_name,
                    request.source,
                    str(request.primary_key),
                ),
            }
        ]

    delete_result = {"deleted_sources": 0, "deleted_chunks": 0}
    if matched_records:
        delete_result = await rag.adelete_custom_kg_sources(matched_records)
        if delete_result.get("status") == "not_allowed":
            raise HTTPException(
                status_code=409,
                detail=delete_result.get(
                    "message",
                    "Custom KG source deletion not allowed",
                ),
            )

    if request.remove_from_state and request.state and matched_records:
        matched_keys = {
            (str(record.get("source")), str(record.get("primary_key")))
            for record in matched_records
        }
        remaining_records = [
            record
            for record in state_records
            if (
                str(record.get("source")),
                str(record.get("primary_key")),
            )
            not in matched_keys
        ]
        _write_json(request.state, remaining_records)

    return {
        "workspace": workspace,
        "source": request.source,
        "primary_key": request.primary_key,
        "matched_records": len(matched_records),
        "delete_result": delete_result,
        "state_updated": bool(
            request.remove_from_state and request.state and matched_records
        ),
    }


def _match_source_delete_records(
    state_records: list[dict[str, Any]],
    request: AuditKGSourceDeleteRequest,
) -> list[dict[str, Any]]:
    source = request.source
    primary_key = str(request.primary_key) if request.primary_key is not None else None
    matched = []
    for record in state_records:
        if str(record.get("source")) != source:
            continue
        if primary_key is not None and str(record.get("primary_key")) != primary_key:
            continue
        matched.append(record)
    return matched


def _source_id(database_name: str, source_name: str, primary_key: str) -> str:
    return f"db://{database_name}/{source_name}/{primary_key}"


def _build_audit_kg_sync(request: AuditKGSyncRequest) -> dict[str, Any]:
    mapping_config = load_mapping_config(request.mapping)
    rows_by_source = ConfiguredSQLSource(
        request.connection_url,
        mapping_config,
    ).load()
    result = ConfigurableKGBuilder(mapping_config).build(rows_by_source)

    previous_records = _read_json_list(request.state) if request.state else []
    sync_diff = diff_sync_records(previous_records, result.sync_records)

    return {
        "mapping_config": mapping_config,
        "rows_by_source": rows_by_source,
        "result": result,
        "sync_diff": sync_diff,
    }


def _read_json_list(path_value: str | None) -> list[dict[str, Any]]:
    if not path_value:
        return []
    path = Path(path_value)
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8") as file:
        data = json.load(file)
    if not isinstance(data, list) or not all(isinstance(item, dict) for item in data):
        raise ValueError(f"Sync state must be a JSON list of objects: {path}")
    return data


def _write_json(path_value: str, data: Any) -> None:
    path = Path(path_value)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as file:
        json.dump(data, file, ensure_ascii=False, indent=2)
        file.write("\n")
