import importlib
import asyncio
import json
import sqlite3
import sys
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import FastAPI, Header, HTTPException
from fastapi.testclient import TestClient


_original_argv = sys.argv[:]
sys.argv = [sys.argv[0]]
try:
    import ascii_colors  # noqa: F401
except ModuleNotFoundError:
    ascii_colors_stub = ModuleType("ascii_colors")

    class _ASCIIColors:
        @staticmethod
        def __getattr__(_name):
            return lambda *_args, **_kwargs: None

        @staticmethod
        def red(*_args, **_kwargs):
            return None

        @staticmethod
        def yellow(*_args, **_kwargs):
            return None

        @staticmethod
        def white(*_args, **_kwargs):
            return None

    ascii_colors_stub.ASCIIColors = _ASCIIColors
    sys.modules["ascii_colors"] = ascii_colors_stub
_audit_routes = importlib.import_module("lightrag.api.routers.audit_routes")
sys.argv = _original_argv

create_audit_routes = _audit_routes.create_audit_routes

pytestmark = pytest.mark.offline

_API_KEY = "test-key"
_HEADERS = {"X-API-Key": _API_KEY}


def _write_mapping(path):
    path.write_text(
        """
schema_version: audit_kg_v1
database_name: audit
sources:
  enterprise:
    primary_key: enterprise_id
entity_types:
  Organization:
    id_prefix: Organization
entities:
  - source: enterprise
    entity_type: Organization
    id_field: enterprise_id
    name_field: enterprise_name
    description_template: "{enterprise_name}; business_scope={business_scope}"
    metadata_fields: "*"
relationships: []
""",
        encoding="utf-8",
    )


def _write_db(path):
    with sqlite3.connect(path) as conn:
        conn.executescript(
            """
            CREATE TABLE enterprise (
                enterprise_id TEXT PRIMARY KEY,
                enterprise_name TEXT,
                business_scope TEXT
            );
            INSERT INTO enterprise VALUES ('E001', 'Huaxin', 'system integration');
            """
        )


def _mapping_dict():
    return {
        "schema_version": "audit_kg_v1",
        "database_name": "audit",
        "sources": {
            "enterprise": {
                "primary_key": "enterprise_id",
            },
        },
        "entity_types": {
            "Organization": {
                "id_prefix": "Organization",
            },
        },
        "entities": [
            {
                "source": "enterprise",
                "entity_type": "Organization",
                "id_field": "enterprise_id",
                "name_field": "enterprise_name",
                "description_template": (
                    "{enterprise_name}; business_scope={business_scope}"
                ),
                "metadata_fields": "*",
            },
        ],
        "relationships": [],
    }


def _build_client(rag):
    async def _auth(x_api_key: str | None = Header(default=None)):
        if x_api_key != _API_KEY:
            raise HTTPException(status_code=403, detail="Invalid API Key")

    app = FastAPI()
    app.include_router(create_audit_routes(rag, auth_dependency=_auth))
    return TestClient(app)


def test_parse_llm_json_object_accepts_markdown_json_block():
    parsed = _audit_routes._parse_llm_json_object(
        '```json\n{"entity_labels":[{"entity_type":"BidRecord","label":"投标记录"}]}\n```'
    )

    assert parsed == {
        "entity_labels": [
            {"entity_type": "BidRecord", "label": "投标记录"},
        ]
    }


def test_audit_kg_sync_dry_run_returns_summary_without_applying(tmp_path):
    mapping_path = tmp_path / "mapping.yaml"
    db_path = tmp_path / "audit.db"
    state_path = tmp_path / "state.json"
    _write_mapping(mapping_path)
    _write_db(db_path)

    rag = SimpleNamespace(workspace="audit_customer_ys", ainsert_custom_kg=AsyncMock())
    client = _build_client(rag)

    response = client.post(
        "/audit/kg-sync",
        headers=_HEADERS,
        json={
            "mapping": str(mapping_path),
            "connection_url": f"sqlite:///{db_path}",
            "state": str(state_path),
            "output": None,
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["applied"] is False
    assert payload["workspace"] == "audit_customer_ys"
    assert payload["sources"] == {"enterprise": 1}
    assert payload["custom_kg"] == {
        "chunks": 1,
        "entities": 1,
        "relationships": 0,
    }
    assert payload["sync_diff"]["insert"] == 1
    rag.ainsert_custom_kg.assert_not_called()
    assert not state_path.exists()


def test_audit_kg_sync_apply_writes_state_and_calls_lightrag(tmp_path):
    mapping_path = tmp_path / "mapping.yaml"
    db_path = tmp_path / "audit.db"
    state_path = tmp_path / "state.json"
    output_path = tmp_path / "payload.json"
    _write_mapping(mapping_path)
    _write_db(db_path)

    rag = SimpleNamespace(
        workspace="audit_customer_ys",
        ainsert_custom_kg=AsyncMock(),
        aclear_cache=AsyncMock(),
    )
    client = _build_client(rag)

    response = client.post(
        "/audit/kg-sync",
        headers=_HEADERS,
        json={
            "mapping": str(mapping_path),
            "connection_url": f"sqlite:///{db_path}",
            "state": str(state_path),
            "output": str(output_path),
            "apply": True,
            "write_state": True,
            "workspace": "audit_customer_ys",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["applied"] is True
    assert payload["apply_result"] == {
        "inserted_chunks": 1,
        "inserted_entities": 1,
        "inserted_relationships": 0,
    }
    rag.ainsert_custom_kg.assert_awaited_once()
    rag.aclear_cache.assert_awaited_once()
    custom_kg = rag.ainsert_custom_kg.await_args.args[0]
    assert custom_kg["entities"][0]["entity_name"] == "Organization:E001"
    assert "business_scope=system integration" in custom_kg["entities"][0][
        "description"
    ]

    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert len(state) == 1
    output = json.loads(output_path.read_text(encoding="utf-8"))
    assert output["summary"]["applied"] is True


def test_generated_mapping_publish_uses_default_state_path_when_state_omitted(
    tmp_path,
    monkeypatch,
):
    db_path = tmp_path / "audit.db"
    _write_db(db_path)
    captured = {}

    async def fake_run_audit_kg_sync(_rag, request):
        captured["request"] = request
        return {
            "applied": request.apply,
            "workspace": request.workspace,
            "sync_diff": {
                "insert": 1,
                "update": 0,
                "delete": 0,
                "unchanged": 0,
            },
        }

    monkeypatch.setattr(_audit_routes, "_run_audit_kg_sync", fake_run_audit_kg_sync)

    rag = SimpleNamespace(workspace="audit_customer_ys")
    client = _build_client(rag)
    generate_response = client.post(
        "/audit/kg-mapping/generate",
        headers=_HEADERS,
        json={
            "connection_url": f"sqlite:///{db_path}",
            "schema": "public",
            "database_name": "audit_default_state_test",
            "workspace": "audit_customer_ys",
            "mode": "review_only",
            "mapping_dir": str(tmp_path / "mappings"),
            "record_dir": str(tmp_path / "records"),
            "prebuilt_mapping": _mapping_dict(),
        },
    )
    assert generate_response.status_code == 200

    generation_id = generate_response.json()["generation_id"]
    publish_response = client.post(
        f"/audit/kg-mapping/generation/{generation_id}/publish"
        f"?record_dir={tmp_path / 'records'}",
        headers=_HEADERS,
        json={
            "apply": True,
            "write_state": True,
        },
    )

    assert publish_response.status_code == 200
    request = captured["request"]
    assert request.write_state is True
    assert request.state == "/app/data/audit_kg_sync/audit_kg_state_server.json"


def test_audit_kg_sync_apply_deletes_removed_rows_before_upsert(tmp_path):
    mapping_path = tmp_path / "mapping.yaml"
    db_path = tmp_path / "audit.db"
    state_path = tmp_path / "state.json"
    _write_mapping(mapping_path)
    _write_db(db_path)
    state_path.write_text(
        json.dumps(
            [
                {
                    "source": "enterprise",
                    "primary_key": "E000",
                    "source_id": "db://audit/enterprise/E000",
                    "row_hash": "old-hash",
                    "entities": ["Organization:E000"],
                    "relationships": [],
                    "chunks": ["db://audit/enterprise/E000"],
                    "chunk_ids": ["chunk-old-e000"],
                }
            ]
        ),
        encoding="utf-8",
    )

    calls = []

    async def _delete(records):
        calls.append("delete")
        assert records[0]["primary_key"] == "E000"
        return {"deleted_sources": 1, "deleted_chunks": 1}

    async def _insert(custom_kg):
        calls.append("insert")
        assert custom_kg["entities"][0]["entity_name"] == "Organization:E001"

    rag = SimpleNamespace(
        workspace="audit_customer_ys",
        adelete_custom_kg_sources=AsyncMock(side_effect=_delete),
        ainsert_custom_kg=AsyncMock(side_effect=_insert),
    )
    client = _build_client(rag)

    response = client.post(
        "/audit/kg-sync",
        headers=_HEADERS,
        json={
            "mapping": str(mapping_path),
            "connection_url": f"sqlite:///{db_path}",
            "state": str(state_path),
            "output": None,
            "apply": True,
            "write_state": True,
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert calls == ["delete", "insert"]
    assert payload["delete_result"] == {"deleted_sources": 1, "deleted_chunks": 1}
    assert payload["sync_diff"]["delete"] == 1
    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert [record["primary_key"] for record in state] == ["E001"]


def test_audit_kg_sync_apply_deletes_updated_rows_before_upsert(tmp_path):
    mapping_path = tmp_path / "mapping.yaml"
    db_path = tmp_path / "audit.db"
    state_path = tmp_path / "state.json"
    _write_mapping(mapping_path)
    _write_db(db_path)
    state_path.write_text(
        json.dumps(
            [
                {
                    "source": "enterprise",
                    "primary_key": "E001",
                    "source_id": "db://audit/enterprise/E001",
                    "row_hash": "old-hash",
                    "entities": ["Old Enterprise"],
                    "relationships": [],
                    "chunks": ["db://audit/enterprise/E001"],
                    "chunk_ids": ["chunk-old-e001"],
                }
            ]
        ),
        encoding="utf-8",
    )

    calls = []

    async def _delete(records):
        calls.append("delete")
        assert [record["primary_key"] for record in records] == ["E001"]
        assert records[0]["entities"] == ["Old Enterprise"]
        return {"deleted_sources": 1, "deleted_chunks": 1}

    async def _insert(custom_kg):
        calls.append("insert")
        assert custom_kg["entities"][0]["entity_name"] == "Organization:E001"

    rag = SimpleNamespace(
        workspace="audit_customer_ys",
        adelete_custom_kg_sources=AsyncMock(side_effect=_delete),
        ainsert_custom_kg=AsyncMock(side_effect=_insert),
    )
    client = _build_client(rag)

    response = client.post(
        "/audit/kg-sync",
        headers=_HEADERS,
        json={
            "mapping": str(mapping_path),
            "connection_url": f"sqlite:///{db_path}",
            "state": str(state_path),
            "output": None,
            "apply": True,
            "write_state": True,
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert calls == ["delete", "insert"]
    assert payload["sync_diff"]["update"] == 1
    assert payload["delete_result"] == {"deleted_sources": 1, "deleted_chunks": 1}
    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert state[0]["entities"] == ["Organization:E001"]


def test_audit_kg_sync_delete_failure_does_not_write_state_or_upsert(tmp_path):
    mapping_path = tmp_path / "mapping.yaml"
    db_path = tmp_path / "audit.db"
    state_path = tmp_path / "state.json"
    _write_mapping(mapping_path)
    _write_db(db_path)
    original_state = [
        {
            "source": "enterprise",
            "primary_key": "E000",
            "source_id": "db://audit/enterprise/E000",
            "row_hash": "old-hash",
            "entities": ["Organization:E000"],
            "relationships": [],
            "chunks": ["db://audit/enterprise/E000"],
            "chunk_ids": ["chunk-old-e000"],
        }
    ]
    state_path.write_text(json.dumps(original_state), encoding="utf-8")

    async def _delete(_records):
        raise RuntimeError("delete failed sentinel")

    rag = SimpleNamespace(
        workspace="audit_customer_ys",
        adelete_custom_kg_sources=AsyncMock(side_effect=_delete),
        ainsert_custom_kg=AsyncMock(),
    )
    client = _build_client(rag)

    response = client.post(
        "/audit/kg-sync",
        headers=_HEADERS,
        json={
            "mapping": str(mapping_path),
            "connection_url": f"sqlite:///{db_path}",
            "state": str(state_path),
            "output": None,
            "apply": True,
            "write_state": True,
        },
    )

    assert response.status_code == 500
    assert "delete failed sentinel" in response.json()["detail"]
    rag.ainsert_custom_kg.assert_not_called()
    assert json.loads(state_path.read_text(encoding="utf-8")) == original_state


def test_audit_kg_sync_delete_not_allowed_does_not_write_state_or_upsert(tmp_path):
    mapping_path = tmp_path / "mapping.yaml"
    db_path = tmp_path / "audit.db"
    state_path = tmp_path / "state.json"
    _write_mapping(mapping_path)
    _write_db(db_path)
    original_state = [
        {
            "source": "enterprise",
            "primary_key": "E000",
            "source_id": "db://audit/enterprise/E000",
            "row_hash": "old-hash",
            "entities": ["Organization:E000"],
            "relationships": [],
            "chunks": ["db://audit/enterprise/E000"],
            "chunk_ids": ["chunk-old-e000"],
        }
    ]
    state_path.write_text(json.dumps(original_state), encoding="utf-8")

    rag = SimpleNamespace(
        workspace="audit_customer_ys",
        adelete_custom_kg_sources=AsyncMock(
            return_value={
                "status": "not_allowed",
                "message": "Custom KG source deletion not allowed",
                "deleted_sources": 0,
                "deleted_chunks": 0,
            }
        ),
        ainsert_custom_kg=AsyncMock(),
    )
    client = _build_client(rag)

    response = client.post(
        "/audit/kg-sync",
        headers=_HEADERS,
        json={
            "mapping": str(mapping_path),
            "connection_url": f"sqlite:///{db_path}",
            "state": str(state_path),
            "output": None,
            "apply": True,
            "write_state": True,
        },
    )

    assert response.status_code == 409
    assert "not allowed" in response.json()["detail"]
    rag.ainsert_custom_kg.assert_not_called()
    assert json.loads(state_path.read_text(encoding="utf-8")) == original_state


def test_audit_kg_sync_rejects_workspace_mismatch(tmp_path):
    mapping_path = tmp_path / "mapping.yaml"
    db_path = tmp_path / "audit.db"
    _write_mapping(mapping_path)
    _write_db(db_path)

    rag = SimpleNamespace(workspace="audit_customer_ys", ainsert_custom_kg=AsyncMock())
    client = _build_client(rag)

    response = client.post(
        "/audit/kg-sync",
        headers=_HEADERS,
        json={
            "mapping": str(mapping_path),
            "connection_url": f"sqlite:///{db_path}",
            "apply": True,
            "workspace": "other_customer",
        },
    )

    assert response.status_code == 400
    assert "does not match" in response.json()["detail"]
    rag.ainsert_custom_kg.assert_not_called()


def test_audit_kg_source_delete_removes_one_source_row_and_state(tmp_path):
    state_path = tmp_path / "state.json"
    state_path.write_text(
        json.dumps(
            [
                {
                    "source": "enterprise_shareholding",
                    "primary_key": "1",
                    "source_id": "db://audit/enterprise_shareholding/1",
                    "row_hash": "hash-1",
                    "entities": ["ShareholdingRecord:1"],
                    "relationships": [],
                    "chunks": ["db://audit/enterprise_shareholding/1"],
                    "chunk_ids": ["chunk-1"],
                },
                {
                    "source": "enterprise_shareholding",
                    "primary_key": "2",
                    "source_id": "db://audit/enterprise_shareholding/2",
                    "row_hash": "hash-2",
                    "entities": ["ShareholdingRecord:2"],
                    "relationships": [],
                    "chunks": ["db://audit/enterprise_shareholding/2"],
                    "chunk_ids": ["chunk-2"],
                },
            ]
        ),
        encoding="utf-8",
    )

    rag = SimpleNamespace(
        workspace="audit_customer_ys",
        adelete_custom_kg_sources=AsyncMock(
            return_value={"deleted_sources": 1, "deleted_chunks": 1}
        ),
    )
    client = _build_client(rag)

    response = client.post(
        "/audit/kg-source/delete",
        headers=_HEADERS,
        json={
            "source": "enterprise_shareholding",
            "primary_key": "1",
            "state": str(state_path),
            "remove_from_state": True,
            "workspace": "audit_customer_ys",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["matched_records"] == 1
    assert payload["delete_result"] == {"deleted_sources": 1, "deleted_chunks": 1}
    rag.adelete_custom_kg_sources.assert_awaited_once()
    records = rag.adelete_custom_kg_sources.await_args.args[0]
    assert records == [
        {
            "source": "enterprise_shareholding",
            "primary_key": "1",
            "source_id": "db://audit/enterprise_shareholding/1",
            "row_hash": "hash-1",
            "entities": ["ShareholdingRecord:1"],
            "relationships": [],
            "chunks": ["db://audit/enterprise_shareholding/1"],
            "chunk_ids": ["chunk-1"],
        }
    ]
    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert [record["primary_key"] for record in state] == ["2"]


def test_audit_kg_source_delete_removes_all_records_for_source(tmp_path):
    state_path = tmp_path / "state.json"
    state_path.write_text(
        json.dumps(
            [
                {
                    "source": "enterprise_shareholding",
                    "primary_key": "1",
                    "source_id": "db://audit/enterprise_shareholding/1",
                    "row_hash": "hash-1",
                    "entities": [],
                    "relationships": [],
                    "chunks": ["db://audit/enterprise_shareholding/1"],
                    "chunk_ids": ["chunk-1"],
                },
                {
                    "source": "enterprise",
                    "primary_key": "E001",
                    "source_id": "db://audit/enterprise/E001",
                    "row_hash": "hash-e001",
                    "entities": [],
                    "relationships": [],
                    "chunks": ["db://audit/enterprise/E001"],
                    "chunk_ids": ["chunk-e001"],
                },
            ]
        ),
        encoding="utf-8",
    )

    rag = SimpleNamespace(
        workspace="audit_customer_ys",
        adelete_custom_kg_sources=AsyncMock(
            return_value={"deleted_sources": 1, "deleted_chunks": 1}
        ),
    )
    client = _build_client(rag)

    response = client.post(
        "/audit/kg-source/delete",
        headers=_HEADERS,
        json={
            "source": "enterprise_shareholding",
            "state": str(state_path),
            "remove_from_state": True,
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["matched_records"] == 1
    records = rag.adelete_custom_kg_sources.await_args.args[0]
    assert records[0]["source_id"] == "db://audit/enterprise_shareholding/1"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert [record["source"] for record in state] == ["enterprise"]


def test_audit_kg_source_delete_rejects_workspace_mismatch(tmp_path):
    state_path = tmp_path / "state.json"
    state_path.write_text("[]", encoding="utf-8")
    rag = SimpleNamespace(
        workspace="audit_customer_ys",
        adelete_custom_kg_sources=AsyncMock(),
    )
    client = _build_client(rag)

    response = client.post(
        "/audit/kg-source/delete",
        headers=_HEADERS,
        json={
            "source": "enterprise_shareholding",
            "state": str(state_path),
            "workspace": "other_customer",
        },
    )

    assert response.status_code == 400
    assert "does not match" in response.json()["detail"]
    rag.adelete_custom_kg_sources.assert_not_called()


def test_audit_kg_source_delete_returns_conflict_when_not_allowed(tmp_path):
    state_path = tmp_path / "state.json"
    original_state = [
        {
            "source": "enterprise_shareholding",
            "primary_key": "1",
            "source_id": "db://audit/enterprise_shareholding/1",
            "row_hash": "hash-1",
            "entities": [],
            "relationships": [],
            "chunks": ["db://audit/enterprise_shareholding/1"],
            "chunk_ids": ["chunk-1"],
        }
    ]
    state_path.write_text(json.dumps(original_state), encoding="utf-8")
    rag = SimpleNamespace(
        workspace="audit_customer_ys",
        adelete_custom_kg_sources=AsyncMock(
            return_value={
                "status": "not_allowed",
                "message": "Custom KG source deletion not allowed",
                "deleted_sources": 0,
                "deleted_chunks": 0,
            }
        ),
    )
    client = _build_client(rag)

    response = client.post(
        "/audit/kg-source/delete",
        headers=_HEADERS,
        json={
            "source": "enterprise_shareholding",
            "primary_key": "1",
            "state": str(state_path),
            "remove_from_state": True,
        },
    )

    assert response.status_code == 409
    assert "not allowed" in response.json()["detail"]
    assert json.loads(state_path.read_text(encoding="utf-8")) == original_state


def test_audit_kg_sync_runs_sync_loader_outside_api_event_loop(monkeypatch, tmp_path):
    mapping_path = tmp_path / "mapping.yaml"
    state_path = tmp_path / "state.json"
    _write_mapping(mapping_path)

    original_loader = _audit_routes.ConfiguredSQLSource

    class EventLoopOwningSQLSource(original_loader):
        def load(self):
            async def _load():
                return {
                    "enterprise": [
                        {
                            "enterprise_id": "E001",
                            "enterprise_name": "Huaxin",
                            "business_scope": "system integration",
                        }
                    ]
                }

            return asyncio.run(_load())

    monkeypatch.setattr(_audit_routes, "ConfiguredSQLSource", EventLoopOwningSQLSource)

    rag = SimpleNamespace(workspace="audit_customer_ys", ainsert_custom_kg=AsyncMock())
    client = _build_client(rag)

    response = client.post(
        "/audit/kg-sync",
        headers=_HEADERS,
        json={
            "mapping": str(mapping_path),
            "connection_url": "postgresql://rag:rag@postgres:5432/audit",
            "state": str(state_path),
            "output": None,
        },
    )

    assert response.status_code == 200
    assert response.json()["custom_kg"]["entities"] == 1
