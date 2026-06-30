"""FastAPI routes for automatic KG mapping generation."""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable, Literal

from fastapi import APIRouter, Body, Depends, HTTPException, Request, UploadFile
from pydantic import BaseModel, Field, model_validator

from .auto_generator import (
    apply_llm_mapping_enhancement,
    filter_schema_for_generation,
    generate_mapping_from_schema,
    generation_summary,
    infer_relationships,
    introspect_postgres_schema,
    load_generation_record,
    merge_mapping_configs,
    metadata_file_hash,
    new_generation_id,
    parse_relationship_metadata_file,
    save_generation_record,
    serialize_relationships,
    write_current_mapping,
    write_mapping_yaml,
)
from .config import load_mapping_config
from .builder import ConfigurableKGBuilder
from .sql_source import ConfiguredSQLSource


SyncCallback = Callable[
    [str, str, str | None, bool, bool, str | None],
    Awaitable[dict[str, Any]],
]
LLMEnhancer = Callable[[dict[str, Any]], Awaitable[dict[str, Any]]]


class KGMappingGenerateRequest(BaseModel):
    connection_url: str = Field(min_length=1)
    db_schema: str = "public"
    database_name: str = Field(min_length=1)
    workspace: str | None = None
    mode: Literal["merge", "full_replace", "review_only"] = "merge"
    business_domain: str = "generic"
    mapping_dir: str | None = None
    record_dir: str | None = None
    sample_limit: int = Field(default=500, ge=1, le=10000)
    auto_approve_threshold: float = Field(default=0.85, ge=0, le=1)
    review_threshold: float = Field(default=0.65, ge=0, le=1)
    excluded_tables: list[str] = Field(default_factory=list)
    excluded_table_patterns: list[str] = Field(default_factory=list)
    enable_llm_enhancement: bool = False
    prebuilt_mapping: dict[str, Any] | None = None

    @model_validator(mode="before")
    @classmethod
    def _accept_schema_alias(cls, data: Any) -> Any:
        if isinstance(data, dict) and "schema" in data and "db_schema" not in data:
            data = dict(data)
            data["db_schema"] = data.pop("schema")
        return data


class KGMappingPublishRequest(BaseModel):
    apply: bool = True
    write_state: bool = True
    state: str | None = None


class KGMappingRollbackRequest(BaseModel):
    apply: bool = True
    write_state: bool = True
    state: str | None = None


def create_kg_mapping_generation_router(
    *,
    auth_dependency=None,
    default_mapping_dir: str = "/app/data/kg_mappings",
    default_record_dir: str = "/app/data/kg_mapping_generations",
    default_excluded_tables: list[str] | None = None,
    default_excluded_table_patterns: list[str] | None = None,
    sync_callback: SyncCallback | None = None,
    llm_enhancer: LLMEnhancer | None = None,
) -> APIRouter:
    router = APIRouter(tags=["kg-mapping-generation"])
    dependencies = [Depends(auth_dependency)] if auth_dependency is not None else []
    default_excluded_tables = default_excluded_tables or []
    default_excluded_table_patterns = default_excluded_table_patterns or []

    @router.post("/audit/kg-mapping/generate", dependencies=dependencies)
    async def generate_mapping(http_request: Request):
        request, uploaded_metadata = await _parse_generate_request(http_request)
        payload = request.model_copy(
            update={
                "mapping_dir": request.mapping_dir or default_mapping_dir,
                "record_dir": request.record_dir or default_record_dir,
                "excluded_tables": [
                    *default_excluded_tables,
                    *request.excluded_tables,
                ],
                "excluded_table_patterns": [
                    *default_excluded_table_patterns,
                    *request.excluded_table_patterns,
                ],
            }
        )
        return await _run_generate_mapping_record(
            payload,
            uploaded_metadata,
            llm_enhancer,
        )

    @router.get(
        "/audit/kg-mapping/generation/{generation_id}",
        dependencies=dependencies,
    )
    async def generation_detail(generation_id: str, record_dir: str | None = None):
        try:
            return await asyncio.to_thread(
                load_generation_record,
                record_dir or default_record_dir,
                generation_id,
            )
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Generation record not found") from exc

    @router.post(
        "/audit/kg-mapping/generation/{generation_id}/preview",
        dependencies=dependencies,
    )
    async def generation_preview(generation_id: str, record_dir: str | None = None):
        try:
            record = await asyncio.to_thread(
                load_generation_record,
                record_dir or default_record_dir,
                generation_id,
            )
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Generation record not found") from exc
        return await asyncio.to_thread(_preview_generation_record, record)

    @router.post(
        "/audit/kg-mapping/generation/{generation_id}/publish",
        dependencies=dependencies,
    )
    async def generation_publish(
        generation_id: str,
        request: KGMappingPublishRequest,
        record_dir: str | None = None,
    ):
        if sync_callback is None:
            raise HTTPException(status_code=501, detail="Publish callback is not configured")
        try:
            record = await asyncio.to_thread(
                load_generation_record,
                record_dir or default_record_dir,
                generation_id,
            )
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Generation record not found") from exc
        if record.get("summary", {}).get("blocked", 0):
            raise HTTPException(status_code=400, detail="Generation has blocked items")
        result = await sync_callback(
            record["mapping_path"],
            record["connection_url"],
            record.get("workspace"),
            request.apply,
            request.write_state,
            request.state,
        )
        previous_published = await asyncio.to_thread(
            _find_latest_published_record,
            record_dir or default_record_dir,
            record["database_name"],
            exclude_generation_id=generation_id,
        )
        current_path = await asyncio.to_thread(
            write_current_mapping,
            Path(record["mapping_path"]).parent,
            record["database_name"],
            record["mapping_path"],
        )
        now = _utc_now()
        record["publish_status"] = "published"
        record["published_at"] = now
        record["published_mapping_path"] = str(current_path)
        record["sync_result"] = result
        if previous_published:
            record["previous_published_generation_id"] = previous_published[
                "generation_id"
            ]
        await asyncio.to_thread(
            save_generation_record,
            record_dir or default_record_dir,
            record,
        )
        return {
            "code": 200,
            "msg": "Publish succeeded",
            "generation_id": generation_id,
            "current_mapping_path": str(current_path),
            "previous_published_generation_id": record.get(
                "previous_published_generation_id"
            ),
            **result,
        }

    @router.post(
        "/audit/kg-mapping/generation/{generation_id}/rollback",
        dependencies=dependencies,
    )
    async def generation_rollback(
        generation_id: str,
        request: KGMappingRollbackRequest | None = Body(default=None),
        record_dir: str | None = None,
    ):
        try:
            record = await asyncio.to_thread(
                load_generation_record,
                record_dir or default_record_dir,
                generation_id,
            )
        except FileNotFoundError as exc:
            raise HTTPException(status_code=404, detail="Generation record not found") from exc
        request = request or KGMappingRollbackRequest()
        if sync_callback is None:
            raise HTTPException(status_code=501, detail="Rollback callback is not configured")
        previous_generation_id = record.get("previous_published_generation_id")
        if not previous_generation_id:
            raise HTTPException(
                status_code=400,
                detail="No previous published generation is available for rollback",
            )
        try:
            previous_record = await asyncio.to_thread(
                load_generation_record,
                record_dir or default_record_dir,
                previous_generation_id,
            )
        except FileNotFoundError as exc:
            raise HTTPException(
                status_code=404,
                detail="Previous generation record not found",
            ) from exc
        result = await sync_callback(
            previous_record["mapping_path"],
            previous_record["connection_url"],
            previous_record.get("workspace"),
            request.apply,
            request.write_state,
            request.state,
        )
        current_path = await asyncio.to_thread(
            write_current_mapping,
            Path(previous_record["mapping_path"]).parent,
            previous_record["database_name"],
            previous_record["mapping_path"],
        )
        now = _utc_now()
        record["publish_status"] = "rolled_back"
        record["rolled_back_at"] = now
        record["rolled_back_to_generation_id"] = previous_generation_id
        record["rollback_sync_result"] = result
        record.pop("published_mapping_path", None)
        previous_record["publish_status"] = "published_after_rollback"
        previous_record["published_mapping_path"] = str(current_path)
        previous_record["republished_at"] = now
        previous_record["rollback_from_generation_id"] = generation_id
        previous_record["rollback_sync_result"] = result
        await asyncio.to_thread(
            save_generation_record,
            record_dir or default_record_dir,
            record,
        )
        await asyncio.to_thread(
            save_generation_record,
            record_dir or default_record_dir,
            previous_record,
        )
        return {
            "code": 200,
            "msg": "Rollback succeeded",
            "generation_id": generation_id,
            "rolled_back_to_generation_id": previous_generation_id,
            "current_mapping_path": str(current_path),
            "sync_result": result,
        }

    return router


async def _parse_generate_request(
    http_request: Request,
) -> tuple[KGMappingGenerateRequest, list[dict[str, Any]]]:
    content_type = http_request.headers.get("content-type", "")
    if content_type.startswith("multipart/form-data"):
        form = await http_request.form()
        payload: dict[str, Any] = {}
        for key in (
            "connection_url",
            "schema",
            "db_schema",
            "database_name",
            "workspace",
            "mode",
            "business_domain",
            "mapping_dir",
            "record_dir",
            "sample_limit",
            "auto_approve_threshold",
            "review_threshold",
            "enable_llm_enhancement",
        ):
            value = form.get(key)
            if value is not None and not hasattr(value, "filename"):
                payload[key] = value
        for key in ("excluded_tables", "excluded_table_patterns"):
            values = [
                str(value)
                for value in form.getlist(key)
                if value is not None and not hasattr(value, "filename")
            ]
            if values:
                payload[key] = _split_form_list(values)
        uploaded_metadata = []
        for value in form.getlist("metadata_files"):
            if hasattr(value, "read") and hasattr(value, "filename"):
                uploaded_metadata.append(
                    {
                        "filename": value.filename or "metadata",
                        "content": await value.read(),
                    }
                )
        return KGMappingGenerateRequest.model_validate(payload), uploaded_metadata

    data = await http_request.json()
    return KGMappingGenerateRequest.model_validate(data), []


def _split_form_list(values: list[str]) -> list[str]:
    results = []
    for value in values:
        results.extend(
            item.strip()
            for item in value.split(",")
            if item.strip()
        )
    return results


async def _run_generate_mapping_record(
    request: KGMappingGenerateRequest,
    uploaded_metadata: list[dict[str, Any]],
    llm_enhancer: LLMEnhancer | None,
) -> dict[str, Any]:
    if uploaded_metadata:
        response = await asyncio.to_thread(
            _generate_mapping_record,
            request,
            uploaded_metadata,
        )
    else:
        response = await asyncio.to_thread(_generate_mapping_record, request)
    if not request.enable_llm_enhancement:
        return response

    record = await asyncio.to_thread(
        load_generation_record,
        request.record_dir or "/app/data/kg_mapping_generations",
        response["generation_id"],
    )
    if llm_enhancer is None:
        record["llm_enhancement"] = {
            "enabled": True,
            "status": "skipped",
            "reason": "llm_enhancer is not configured",
        }
        await asyncio.to_thread(
            save_generation_record,
            request.record_dir or "/app/data/kg_mapping_generations",
            record,
        )
        response["llm_enhancement"] = _llm_enhancement_response_summary(
            record["llm_enhancement"]
        )
        return response

    payload = _llm_enhancement_payload(record)
    try:
        raw_enhancement = await llm_enhancer(payload)
    except Exception as exc:
        record["llm_enhancement"] = {
            "enabled": True,
            "status": "failed",
            "error": str(exc),
            "payload": payload,
        }
        await asyncio.to_thread(
            save_generation_record,
            request.record_dir or "/app/data/kg_mapping_generations",
            record,
        )
        raise
    enhanced_mapping, trace = apply_llm_mapping_enhancement(
        record["mapping"],
        raw_enhancement,
    )
    record["mapping"] = enhanced_mapping
    record["llm_enhancement"] = {
        "enabled": True,
        "status": "applied",
        "payload": payload,
        "trace": trace,
    }
    await asyncio.to_thread(
        write_mapping_yaml,
        request.mapping_dir or "/app/data/kg_mappings",
        record["database_name"],
        record["generation_id"],
        enhanced_mapping,
    )
    await asyncio.to_thread(
        save_generation_record,
        request.record_dir or "/app/data/kg_mapping_generations",
        record,
    )
    response["llm_enhancement"] = _llm_enhancement_response_summary(
        record["llm_enhancement"]
    )
    return response


def _generate_mapping_record(
    request: KGMappingGenerateRequest,
    uploaded_metadata: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    generation_id = new_generation_id()
    mapping_dir = request.mapping_dir or "/app/data/kg_mappings"
    record_dir = request.record_dir or "/app/data/kg_mapping_generations"
    input_sources: list[dict[str, Any]] = []
    metadata_relationships: list[Any] = []
    all_metadata_table_names: set[str] = set()
    for item in uploaded_metadata or []:
        filename = item["filename"]
        content = item["content"]
        parsed = parse_relationship_metadata_file(filename, content)
        metadata_table_names = sorted(
            {
                table_name
                for relationship in parsed
                for table_name in (
                    relationship.source_table,
                    relationship.target_table,
                )
            }
        )
        all_metadata_table_names.update(metadata_table_names)
        metadata_relationships.extend(parsed)
        input_sources.append(
            {
                "filename": filename,
                "sha256": metadata_file_hash(content),
                "relationships": len(parsed),
                "tables": len(metadata_table_names),
                "table_names": metadata_table_names,
            }
        )

    if request.prebuilt_mapping is not None:
        mapping = request.prebuilt_mapping
        relationships = []
        excluded_tables = []
        tables_count = len(mapping.get("sources", {}))
        table_counts = {
            "discovered": tables_count,
            "included": tables_count,
            "excluded": 0,
        }
        summary = {
            "tables": tables_count,
            "discovered_tables": table_counts["discovered"],
            "included_tables": table_counts["included"],
            "excluded_tables": table_counts["excluded"],
            "entities": len(mapping.get("entities", [])),
            "relationships": len(mapping.get("relationships", [])),
            "auto_approved": len(mapping.get("entities", []))
            + len(mapping.get("relationships", [])),
            "need_review": 0,
            "blocked": 0,
        }
        discovered_table_names = set(mapping.get("sources", {}))
    else:
        tables, explicit_relationships, coverage = introspect_postgres_schema(
            request.connection_url,
            schema=request.db_schema,
            sample_limit=request.sample_limit,
        )
        discovered_tables_count = len(tables)
        discovered_table_names = {table.table_name for table in tables}
        explicit_relationships = [*explicit_relationships, *metadata_relationships]
        tables, explicit_relationships, coverage, excluded_tables = (
            filter_schema_for_generation(
                tables,
                explicit_relationships,
                coverage,
                excluded_tables=set(request.excluded_tables),
                excluded_table_patterns=request.excluded_table_patterns,
            )
        )
        relationships = infer_relationships(
            tables,
            explicit_relationships=explicit_relationships,
            coverage=coverage,
            auto_approve_threshold=request.auto_approve_threshold,
            review_threshold=request.review_threshold,
        )
        mapping = generate_mapping_from_schema(
            database_name=request.database_name,
            tables=tables,
            relationships=relationships,
        )
        if request.mode == "merge":
            current_path = Path(mapping_dir) / f"{request.database_name}.current.yaml"
            if current_path.exists():
                base_mapping = load_mapping_config(current_path).raw
                mapping = merge_mapping_configs(base_mapping, mapping)
        summary = generation_summary(tables, mapping, relationships)
        table_counts = {
            "discovered": discovered_tables_count,
            "included": len(tables),
            "excluded": len(excluded_tables),
        }
        summary.update(
            {
                "discovered_tables": table_counts["discovered"],
                "included_tables": table_counts["included"],
                "excluded_tables": table_counts["excluded"],
            }
        )

    metadata_table_counts = _metadata_table_counts(
        all_metadata_table_names,
        set(mapping.get("sources", {})),
        set(excluded_tables),
        discovered_table_names,
    )

    mapping_path = write_mapping_yaml(
        mapping_dir,
        request.database_name,
        generation_id,
        mapping,
    )
    record = {
        "generation_id": generation_id,
        "database_name": request.database_name,
        "schema": request.db_schema,
        "workspace": request.workspace,
        "mode": request.mode,
        "business_domain": request.business_domain,
        "connection_url": request.connection_url,
        "mapping_path": str(mapping_path),
        "summary": summary,
        "table_counts": table_counts,
        "metadata_table_counts": metadata_table_counts,
        "mapping": mapping,
        "relationships": serialize_relationships(relationships),
        "excluded_tables": excluded_tables,
        "input_sources": input_sources,
        "publish_status": "draft",
        "created_at": _utc_now(),
    }
    record_path = save_generation_record(record_dir, record)

    return {
        "generation_id": generation_id,
        "mapping_path": str(mapping_path),
        "record_path": str(record_path),
        "summary": summary,
        "table_counts": table_counts,
        "metadata_table_counts": metadata_table_counts,
        "input_sources": input_sources,
        "excluded_tables": excluded_tables,
        "can_publish": summary.get("blocked", 0) == 0,
    }


def _metadata_table_counts(
    metadata_table_names: set[str],
    included_table_names: set[str],
    excluded_table_names: set[str],
    discovered_table_names: set[str],
) -> dict[str, Any]:
    included = metadata_table_names & included_table_names
    excluded = metadata_table_names & excluded_table_names
    unmatched = metadata_table_names - included - excluded - discovered_table_names
    return {
        "total": len(metadata_table_names),
        "included": len(included),
        "excluded": len(excluded),
        "unmatched": len(unmatched),
        "table_names": sorted(metadata_table_names),
        "included_table_names": sorted(included),
        "excluded_table_names": sorted(excluded),
        "unmatched_table_names": sorted(unmatched),
    }


def _preview_generation_record(record: dict[str, Any]) -> dict[str, Any]:
    mapping_config = load_mapping_config(record["mapping_path"])
    rows_by_source = ConfiguredSQLSource(
        record["connection_url"],
        mapping_config,
    ).load()
    result = ConfigurableKGBuilder(mapping_config).build(rows_by_source)
    return {
        "sources": {
            source_name: len(rows)
            for source_name, rows in rows_by_source.items()
        },
        "custom_kg": {
            "chunks": len(result.custom_kg["chunks"]),
            "entities": len(result.custom_kg["entities"]),
            "relationships": len(result.custom_kg["relationships"]),
        },
        "sample_entities": result.custom_kg["entities"][:5],
        "sample_relationships": result.custom_kg["relationships"][:5],
        "sample_chunks": result.custom_kg["chunks"][:5],
    }


def _find_latest_published_record(
    record_dir: str | Path,
    database_name: str,
    *,
    exclude_generation_id: str | None = None,
) -> dict[str, Any] | None:
    candidates = []
    for path in Path(record_dir).glob("gen_*.json"):
        try:
            record = load_generation_record(record_dir, path.stem)
        except (OSError, ValueError, json.JSONDecodeError):
            continue
        if record.get("generation_id") == exclude_generation_id:
            continue
        if record.get("database_name") != database_name:
            continue
        if record.get("publish_status") not in {
            "published",
            "published_after_rollback",
        }:
            continue
        candidates.append(record)
    if not candidates:
        return None
    return sorted(
        candidates,
        key=lambda item: (
            item.get("published_at")
            or item.get("republished_at")
            or item.get("created_at")
            or "",
            item.get("generation_id") or "",
        ),
    )[-1]


def _llm_enhancement_payload(record: dict[str, Any]) -> dict[str, Any]:
    mapping = record["mapping"]
    return {
        "database_name": record.get("database_name"),
        "schema": record.get("schema"),
        "business_domain": record.get("business_domain"),
        "sources": mapping.get("sources", {}),
        "entity_types": mapping.get("entity_types", {}),
        "entities": mapping.get("entities", []),
        "relationships": mapping.get("relationships", []),
        "relationship_candidates": record.get("relationships", []),
        "instructions": (
            "Only suggest readability improvements: entity type labels, "
            "entity_name_template, description_template, relation_type. "
            "Do not change source names, SQL queries, primary keys, id fields, "
            "or relationship endpoints."
        ),
    }


def _llm_enhancement_response_summary(
    llm_enhancement: dict[str, Any],
) -> dict[str, Any]:
    summary = {
        "enabled": llm_enhancement.get("enabled", False),
        "status": llm_enhancement.get("status"),
    }
    if "reason" in llm_enhancement:
        summary["reason"] = llm_enhancement["reason"]
    if "error" in llm_enhancement:
        summary["error"] = llm_enhancement["error"]
    trace = llm_enhancement.get("trace")
    if isinstance(trace, dict):
        summary["applied"] = trace.get("applied", {})
        summary["ignored_count"] = len(trace.get("ignored", []) or [])
    return summary


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()
