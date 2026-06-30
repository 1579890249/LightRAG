from types import SimpleNamespace
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, patch

import pytest

from lightrag.lightrag import LightRAG, _find_storage_chunk_ids_by_full_doc_id
from lightrag.namespace import NameSpace
from lightrag.utils import compute_mdhash_id, make_relation_chunk_key

pytestmark = pytest.mark.offline


class _Tokenizer:
    def encode(self, _content):
        return []


def _storage_mock(existing=None):
    existing = existing or {}
    storage = SimpleNamespace()
    storage.upsert = AsyncMock()
    storage.delete = AsyncMock()
    storage.get_by_id = AsyncMock(side_effect=lambda key: existing.get(key))
    storage.get_by_ids = AsyncMock(
        side_effect=lambda keys: [existing.get(key) for key in keys]
    )
    return storage


class _ScanKVStorage:
    def __init__(self, existing):
        self._data = existing
        self.upsert = AsyncMock()
        self.delete = AsyncMock()
        self.get_by_id = AsyncMock(side_effect=lambda key: existing.get(key))
        self.get_by_ids = AsyncMock(
            side_effect=lambda keys: [existing.get(key) for key in keys]
        )


class _ScanVectorStorage(_ScanKVStorage):
    pass


class _PGDB:
    def __init__(self, rows):
        self.rows = rows
        self.query = AsyncMock(return_value=rows)


class _MilvusClient:
    def __init__(self, rows):
        self.rows = rows
        self.calls = []

    def query(self, **kwargs):
        self.calls.append(kwargs)
        return self.rows


def _graph_mock():
    graph = SimpleNamespace()
    graph.upsert_nodes_batch = AsyncMock()
    graph.upsert_edges_batch = AsyncMock()
    graph.has_nodes_batch = AsyncMock(return_value=set())
    return graph


def _make_rag():
    rag = LightRAG.__new__(LightRAG)
    rag.workspace = "test-workspace"
    rag.tokenizer = _Tokenizer()
    rag.chunks_vdb = _storage_mock()
    rag.text_chunks = _storage_mock()
    rag.full_entities = _storage_mock()
    rag.full_relations = _storage_mock()
    rag.entity_chunks = _storage_mock()
    rag.relation_chunks = _storage_mock()
    rag.chunk_entity_relation_graph = _graph_mock()
    rag.entities_vdb = _storage_mock()
    rag.relationships_vdb = _storage_mock()
    rag._insert_done = AsyncMock()
    rag._insert_done_with_cleanup = AsyncMock()
    return rag


@asynccontextmanager
async def _noop_lock():
    yield


class _ReusableNoopLock:
    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        return False


@pytest.mark.asyncio
async def test_find_storage_chunk_ids_by_full_doc_id_uses_pg_text_chunk_query():
    storage = SimpleNamespace(
        namespace=NameSpace.KV_STORE_TEXT_CHUNKS,
        db=_PGDB([{"id": "chunk-a"}, {"id": "chunk-b"}]),
    )

    chunk_ids = await _find_storage_chunk_ids_by_full_doc_id(
        storage,
        "db://audit/person_relation/R2",
        "audit_customer_ys",
    )

    assert chunk_ids == ["chunk-a", "chunk-b"]
    storage.db.query.assert_awaited_once_with(
        (
            "SELECT id FROM LIGHTRAG_DOC_CHUNKS "
            "WHERE workspace=$1 AND full_doc_id=$2 ORDER BY id"
        ),
        ["audit_customer_ys", "db://audit/person_relation/R2"],
        multirows=True,
    )


@pytest.mark.asyncio
async def test_find_storage_chunk_ids_by_full_doc_id_uses_milvus_full_doc_query():
    client = _MilvusClient([{"id": "chunk-vector-a"}, {"id": "chunk-vector-b"}])
    storage = SimpleNamespace(
        namespace=NameSpace.VECTOR_STORE_CHUNKS,
        meta_fields={"full_doc_id", "content"},
        final_namespace="audit_customer_ys_chunks",
        _client=client,
        _ensure_collection_loaded=lambda: None,
    )

    chunk_ids = await _find_storage_chunk_ids_by_full_doc_id(
        storage,
        "db://audit/person_relation/R2",
        "audit_customer_ys",
    )

    assert chunk_ids == ["chunk-vector-a", "chunk-vector-b"]
    assert client.calls == [
        {
            "collection_name": "audit_customer_ys_chunks",
            "filter": 'full_doc_id == "db://audit/person_relation/R2"',
            "output_fields": ["id"],
        }
    ]


@pytest.mark.asyncio
async def test_ainsert_custom_kg_writes_source_row_provenance_indexes():
    rag = _make_rag()
    content = "source=enterprise; enterprise_id=E001"
    source_id = "db://audit/enterprise/E001"
    expected_chunk_id = compute_mdhash_id(content, prefix="chunk-")
    expected_relation_key = make_relation_chunk_key("Organization:E001", "Project:P1")

    with patch("lightrag.lightrag.get_storage_keyed_lock", return_value=_noop_lock()):
        await rag.ainsert_custom_kg(
            {
                "chunks": [
                    {
                        "source_id": source_id,
                        "content": content,
                        "file_path": source_id,
                    }
                ],
                "entities": [
                    {
                        "entity_name": "Organization:E001",
                        "entity_type": "Organization",
                        "description": "Huaxin",
                        "source_id": source_id,
                        "file_path": source_id,
                    }
                ],
                "relationships": [
                    {
                        "src_id": "Organization:E001",
                        "tgt_id": "Project:P1",
                        "keywords": "BIDDER",
                        "description": "Huaxin bids for project P1",
                        "source_id": source_id,
                        "weight": 1.0,
                        "file_path": source_id,
                    }
                ],
            }
        )

    rag.full_entities.upsert.assert_awaited_once_with(
        {
            source_id: {
                "entity_names": ["Organization:E001"],
                "chunk_ids": [expected_chunk_id],
                "count": 1,
            }
        }
    )
    rag.full_relations.upsert.assert_awaited_once_with(
        {
            source_id: {
                "relation_pairs": [["Organization:E001", "Project:P1"]],
                "chunk_ids": [expected_chunk_id],
                "count": 1,
            }
        }
    )
    rag.entity_chunks.upsert.assert_awaited_once()
    entity_payload = rag.entity_chunks.upsert.await_args.args[0]
    assert entity_payload["Organization:E001"]["chunk_ids"] == [expected_chunk_id]
    assert entity_payload["Organization:E001"]["count"] == 1
    rag.relation_chunks.upsert.assert_awaited_once()
    relation_payload = rag.relation_chunks.upsert.await_args.args[0]
    assert relation_payload[expected_relation_key]["chunk_ids"] == [expected_chunk_id]
    assert relation_payload[expected_relation_key]["count"] == 1


@pytest.mark.asyncio
async def test_ainsert_custom_kg_preserves_duplicate_entity_relation_provenance():
    rag = _make_rag()
    first_source = "db://audit/enterprise/E001"
    second_source = "db://audit/bid_record/B1"
    first_content = "source=enterprise; enterprise_id=E001"
    second_content = "source=bid_record; bid_id=B1; enterprise_id=E001"
    first_chunk_id = compute_mdhash_id(first_content, prefix="chunk-")
    second_chunk_id = compute_mdhash_id(second_content, prefix="chunk-")
    relation_key = make_relation_chunk_key("Organization:E001", "Project:P1")

    with patch("lightrag.lightrag.get_storage_keyed_lock", return_value=_noop_lock()):
        await rag.ainsert_custom_kg(
            {
                "chunks": [
                    {
                        "source_id": first_source,
                        "content": first_content,
                        "file_path": first_source,
                    },
                    {
                        "source_id": second_source,
                        "content": second_content,
                        "file_path": second_source,
                    },
                ],
                "entities": [
                    {
                        "entity_name": "Organization:E001",
                        "entity_type": "Organization",
                        "description": "Huaxin from enterprise",
                        "source_id": first_source,
                        "file_path": first_source,
                    },
                    {
                        "entity_name": "Organization:E001",
                        "entity_type": "Organization",
                        "description": "Huaxin from bid",
                        "source_id": second_source,
                        "file_path": second_source,
                    },
                ],
                "relationships": [
                    {
                        "src_id": "Organization:E001",
                        "tgt_id": "Project:P1",
                        "keywords": "PARTICIPATES_IN",
                        "description": "enterprise participates",
                        "source_id": first_source,
                        "weight": 1.0,
                        "file_path": first_source,
                    },
                    {
                        "src_id": "Organization:E001",
                        "tgt_id": "Project:P1",
                        "keywords": "PARTICIPATES_IN",
                        "description": "bid participates",
                        "source_id": second_source,
                        "weight": 1.0,
                        "file_path": second_source,
                    },
                ],
            }
        )

    full_entities_payload = rag.full_entities.upsert.await_args.args[0]
    assert full_entities_payload[first_source] == {
        "entity_names": ["Organization:E001"],
        "chunk_ids": [first_chunk_id],
        "count": 1,
    }
    assert full_entities_payload[second_source] == {
        "entity_names": ["Organization:E001"],
        "chunk_ids": [second_chunk_id],
        "count": 1,
    }
    entity_payload = rag.entity_chunks.upsert.await_args.args[0]
    assert entity_payload["Organization:E001"]["chunk_ids"] == [
        first_chunk_id,
        second_chunk_id,
    ]
    full_relations_payload = rag.full_relations.upsert.await_args.args[0]
    assert full_relations_payload[first_source] == {
        "relation_pairs": [["Organization:E001", "Project:P1"]],
        "chunk_ids": [first_chunk_id],
        "count": 1,
    }
    assert full_relations_payload[second_source] == {
        "relation_pairs": [["Organization:E001", "Project:P1"]],
        "chunk_ids": [second_chunk_id],
        "count": 1,
    }
    relation_payload = rag.relation_chunks.upsert.await_args.args[0]
    assert relation_payload[relation_key]["chunk_ids"] == [
        first_chunk_id,
        second_chunk_id,
    ]


@pytest.mark.asyncio
async def test_adelete_custom_kg_sources_purges_source_row_chunks():
    source_id = "db://audit/enterprise/E001"
    chunk_id = "chunk-row-e001"
    rag = _make_rag()
    rag.full_entities = _storage_mock(
        {source_id: {"entity_names": ["Organization:E001"], "chunk_ids": [chunk_id]}}
    )
    rag.text_chunks = _storage_mock(
        {
            chunk_id: {
                "source_id": source_id,
                "full_doc_id": source_id,
                "content": "source=enterprise; enterprise_id=E001",
            }
        }
    )
    rag._purge_doc_chunks_and_kg = AsyncMock()

    pipeline_status = {"history_messages": []}

    async def _get_namespace_data(*_args, **_kwargs):
        return pipeline_status

    with (
        patch("lightrag.lightrag.get_namespace_data", side_effect=_get_namespace_data),
        patch("lightrag.lightrag.get_namespace_lock", return_value=_ReusableNoopLock()),
    ):
        result = await rag.adelete_custom_kg_sources(
            [
                {
                    "source": "enterprise",
                    "primary_key": "E001",
                    "source_id": source_id,
                    "row_hash": "old-hash",
                    "entities": ["Organization:E001"],
                    "relationships": [],
                    "chunks": [source_id],
                }
            ]
        )

    assert result == {"deleted_sources": 1, "deleted_chunks": 1}
    rag._purge_doc_chunks_and_kg.assert_awaited_once()
    args = rag._purge_doc_chunks_and_kg.await_args.args
    assert args == (source_id, [chunk_id])
    assert "pipeline_status" in rag._purge_doc_chunks_and_kg.await_args.kwargs
    assert "pipeline_status_lock" in rag._purge_doc_chunks_and_kg.await_args.kwargs


@pytest.mark.asyncio
async def test_adelete_custom_kg_sources_purges_all_chunks_with_same_source_row():
    source_id = "db://audit/person_relation/R2"
    tracked_chunk_id = "chunk-current-r2"
    orphan_text_chunk_id = "chunk-old-text-r2"
    orphan_vector_chunk_id = "chunk-old-vector-r2"
    rag = _make_rag()
    rag.full_relations = _storage_mock(
        {
            source_id: {
                "relation_pairs": [["张敏", "李娜"]],
                "chunk_ids": [tracked_chunk_id],
            }
        }
    )
    rag.text_chunks = _ScanKVStorage(
        {
            tracked_chunk_id: {
                "full_doc_id": source_id,
                "content": "current relation chunk",
            },
            orphan_text_chunk_id: {
                "full_doc_id": source_id,
                "content": "old relation chunk from a previous mapping",
            },
            "chunk-other": {
                "full_doc_id": "db://audit/person_relation/R1",
                "content": "another source row",
            },
        }
    )
    rag.chunks_vdb = _ScanVectorStorage(
        {
            orphan_vector_chunk_id: {
                "full_doc_id": source_id,
                "content": "old vector-only relation chunk",
            }
        }
    )
    rag._purge_doc_chunks_and_kg = AsyncMock()

    pipeline_status = {"history_messages": []}

    async def _get_namespace_data(*_args, **_kwargs):
        return pipeline_status

    with (
        patch("lightrag.lightrag.get_namespace_data", side_effect=_get_namespace_data),
        patch("lightrag.lightrag.get_namespace_lock", return_value=_ReusableNoopLock()),
    ):
        result = await rag.adelete_custom_kg_sources(
            [
                {
                    "source": "person_relation",
                    "primary_key": "R2",
                    "source_id": source_id,
                    "row_hash": "old-hash",
                    "entities": [],
                    "relationships": [
                        {
                            "src_id": "张敏",
                            "tgt_id": "李娜",
                            "keywords": "PERSON_RELATED",
                        }
                    ],
                    "chunks": [source_id],
                    "chunk_ids": [tracked_chunk_id],
                }
            ]
        )

    assert result == {"deleted_sources": 1, "deleted_chunks": 3}
    rag._purge_doc_chunks_and_kg.assert_awaited_once()
    args = rag._purge_doc_chunks_and_kg.await_args.args
    assert args[0] == source_id
    assert set(args[1]) == {
        tracked_chunk_id,
        orphan_text_chunk_id,
        orphan_vector_chunk_id,
    }


@pytest.mark.asyncio
async def test_adelete_custom_kg_sources_refuses_when_pipeline_busy():
    rag = _make_rag()
    rag._purge_doc_chunks_and_kg = AsyncMock()
    pipeline_status = {"busy": True, "job_name": "document ingestion"}

    async def _get_namespace_data(*_args, **_kwargs):
        return pipeline_status

    with (
        patch("lightrag.lightrag.get_namespace_data", side_effect=_get_namespace_data),
        patch("lightrag.lightrag.get_namespace_lock", return_value=_ReusableNoopLock()),
    ):
        result = await rag.adelete_custom_kg_sources(
            [
                {
                    "source": "enterprise",
                    "primary_key": "E001",
                    "source_id": "db://audit/enterprise/E001",
                    "row_hash": "old-hash",
                    "entities": ["Organization:E001"],
                    "relationships": [],
                    "chunks": ["db://audit/enterprise/E001"],
                }
            ]
        )

    assert result["status"] == "not_allowed"
    assert "document ingestion" in result["message"]
    rag._purge_doc_chunks_and_kg.assert_not_called()
