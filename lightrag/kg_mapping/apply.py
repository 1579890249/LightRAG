"""Apply configurable KG payloads to a LightRAG instance."""

from __future__ import annotations

from collections.abc import Callable, Awaitable
from contextlib import contextmanager
from dataclasses import dataclass
import sys
from typing import Any


@dataclass(frozen=True)
class ApplyResult:
    inserted_chunks: int
    inserted_entities: int
    inserted_relationships: int


RagFactory = Callable[[str | None], Awaitable[Any]]


async def apply_custom_kg(
    custom_kg: dict[str, list[dict[str, Any]]],
    *,
    workspace: str | None = None,
    rag_factory: RagFactory | None = None,
) -> ApplyResult:
    """Insert a custom_kg payload into a LightRAG workspace."""

    factory = rag_factory or create_server_configured_rag
    rag = await factory(workspace)
    try:
        await rag.ainsert_custom_kg(custom_kg)
    finally:
        finalize = getattr(rag, "finalize_storages", None)
        if finalize is not None:
            await finalize()

    return ApplyResult(
        inserted_chunks=len(custom_kg.get("chunks", [])),
        inserted_entities=len(custom_kg.get("entities", [])),
        inserted_relationships=len(custom_kg.get("relationships", [])),
    )


async def create_server_configured_rag(workspace: str | None = None) -> Any:
    """Create a LightRAG instance using the same env config as the API server."""

    with _api_config_argv():
        from lightrag import LightRAG
        from lightrag.api.config import parse_args
        from lightrag.api.lightrag_server import (
            LLMConfigCache,
            create_embedding_function_from_args,
        )

        args = parse_args()

    if workspace is not None:
        args.workspace = workspace

    config_cache = LLMConfigCache(args)
    embedding_func = create_embedding_function_from_args(args, config_cache)

    async def _unused_llm(*_args, **_kwargs) -> str:
        return ""

    rag = LightRAG(
        working_dir=args.working_dir,
        workspace=args.workspace,
        llm_model_func=_unused_llm,
        llm_model_name=args.llm_model,
        llm_model_max_async=args.max_async,
        summary_max_tokens=args.summary_max_tokens,
        summary_context_size=args.summary_context_size,
        chunk_token_size=int(args.chunk_size),
        chunk_overlap_token_size=int(args.chunk_overlap_size),
        embedding_func=embedding_func,
        default_llm_timeout=args.llm_timeout,
        default_embedding_timeout=args.embedding_timeout,
        kv_storage=args.kv_storage,
        graph_storage=args.graph_storage,
        vector_storage=args.vector_storage,
        doc_status_storage=args.doc_status_storage,
        vector_db_storage_cls_kwargs={
            "cosine_better_than_threshold": args.cosine_threshold
        },
        enable_llm_cache_for_entity_extract=args.enable_llm_cache_for_extract,
        enable_llm_cache=args.enable_llm_cache,
        vlm_process_enable=args.vlm_process_enable,
        max_parallel_insert=args.max_parallel_insert,
        max_graph_nodes=args.max_graph_nodes,
        addon_params={"language": args.summary_language},
    )
    await rag.initialize_storages()
    return rag


@contextmanager
def _api_config_argv():
    original_argv = sys.argv[:]
    sys.argv = [original_argv[0] if original_argv else "audit_kg_sync.py"]
    try:
        yield
    finally:
        sys.argv = original_argv
