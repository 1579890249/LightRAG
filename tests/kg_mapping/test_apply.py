import asyncio
import argparse
import builtins
import sys
from types import SimpleNamespace

from lightrag.kg_mapping.apply import apply_custom_kg
from lightrag.kg_mapping.apply import create_server_configured_rag


class FakeRAG:
    def __init__(self):
        self.inserted = []
        self.finalized = False

    async def ainsert_custom_kg(self, custom_kg):
        self.inserted.append(custom_kg)

    async def finalize_storages(self):
        self.finalized = True


def test_apply_custom_kg_inserts_payload_and_finalizes():
    asyncio.run(_run_apply_custom_kg_inserts_payload_and_finalizes())


async def _run_apply_custom_kg_inserts_payload_and_finalizes():
    created = []
    custom_kg = {
        "chunks": [{"source_id": "db://audit/enterprise/E001"}],
        "entities": [{"entity_name": "Organization:E001"}],
        "relationships": [
            {
                "src_id": "Person:P001",
                "tgt_id": "Organization:E001",
                "keywords": "EMPLOYED_BY",
            }
        ],
    }

    async def factory(workspace):
        assert workspace == "audit_customer_ys"
        rag = FakeRAG()
        created.append(rag)
        return rag

    result = await apply_custom_kg(
        custom_kg,
        workspace="audit_customer_ys",
        rag_factory=factory,
    )

    assert created[0].inserted == [custom_kg]
    assert created[0].finalized is True
    assert result.inserted_chunks == 1
    assert result.inserted_entities == 1
    assert result.inserted_relationships == 1


def test_create_server_configured_rag_sanitizes_args_during_api_import(monkeypatch):
    observed = {}
    original_import = builtins.__import__

    class FakeLightRAG:
        def __init__(self, **kwargs):
            self.kwargs = kwargs
            self.initialized = False

        async def initialize_storages(self):
            self.initialized = True

    class FakeLLMConfigCache:
        def __init__(self, args):
            self.args = args

    def fake_parse_args():
        observed["parse_args_argv"] = sys.argv[:]
        return argparse.Namespace(
            working_dir="/tmp/lightrag",
            workspace="default",
            llm_model="unused",
            max_async=1,
            summary_max_tokens=1000,
            summary_context_size=1000,
            chunk_size=1200,
            chunk_overlap_size=100,
            llm_timeout=30,
            embedding_timeout=30,
            kv_storage="JsonKVStorage",
            graph_storage="NetworkXStorage",
            vector_storage="NanoVectorDBStorage",
            doc_status_storage="JsonDocStatusStorage",
            cosine_threshold=0.2,
            enable_llm_cache_for_extract=True,
            enable_llm_cache=True,
            vlm_process_enable=False,
            max_parallel_insert=1,
            max_graph_nodes=1000,
            summary_language="Chinese",
        )

    def fake_create_embedding_function_from_args(args, config_cache):
        observed["embedding_args"] = args
        observed["embedding_config_cache"] = config_cache
        return "fake_embedding"

    def fake_import(name, globals=None, locals=None, fromlist=(), level=0):
        if name == "lightrag" and "LightRAG" in fromlist:
            return SimpleNamespace(LightRAG=FakeLightRAG)
        if name == "lightrag.api.config":
            return SimpleNamespace(parse_args=fake_parse_args)
        if name == "lightrag.api.lightrag_server":
            observed["server_import_argv"] = sys.argv[:]
            if "--mapping" in sys.argv:
                raise AssertionError("script args leaked into API server import")
            return SimpleNamespace(
                LLMConfigCache=FakeLLMConfigCache,
                create_embedding_function_from_args=fake_create_embedding_function_from_args,
            )
        return original_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "audit_kg_sync.py",
            "--mapping",
            "configs/kg_mappings/audit_customer_ys.yaml",
            "--connection-url",
            "postgresql://rag:rag@postgres:5432/audit",
        ],
    )

    rag = asyncio.run(create_server_configured_rag(workspace="audit_customer_ys"))

    assert observed["server_import_argv"] == ["audit_kg_sync.py"]
    assert observed["parse_args_argv"] == ["audit_kg_sync.py"]
    assert rag.kwargs["workspace"] == "audit_customer_ys"
    assert rag.initialized is True
