import sqlite3
from pathlib import Path

import pytest
from fastapi import FastAPI, Header, HTTPException
from fastapi.testclient import TestClient

from lightrag.kg_mapping.auto_generation_api import (
    create_kg_mapping_generation_router,
)
from lightrag.kg_mapping.auto_generator import (
    ColumnInfo,
    RelationshipCandidate,
    TableInfo,
    generate_mapping_from_schema,
)


pytestmark = pytest.mark.offline

_API_KEY = "test-key"
_HEADERS = {"X-API-Key": _API_KEY}


def _build_client(
    tmp_path,
    sync_callback=None,
    llm_enhancer=None,
    default_excluded_tables=None,
    default_excluded_table_patterns=None,
):
    async def _auth(x_api_key: str | None = Header(default=None)):
        if x_api_key != _API_KEY:
            raise HTTPException(status_code=403, detail="Invalid API Key")

    app = FastAPI()
    app.include_router(
        create_kg_mapping_generation_router(
            auth_dependency=_auth,
            default_mapping_dir=str(tmp_path / "mappings"),
            default_record_dir=str(tmp_path / "records"),
            default_excluded_tables=default_excluded_tables,
            default_excluded_table_patterns=default_excluded_table_patterns,
            sync_callback=sync_callback,
            llm_enhancer=llm_enhancer,
        )
    )
    return TestClient(app)


def _column(table: str, name: str, data_type: str = "text") -> ColumnInfo:
    return ColumnInfo(table_name=table, column_name=name, data_type=data_type)


def _mapping() -> dict:
    tables = [
        TableInfo(
            table_name="company",
            primary_key="company_id",
            columns=[_column("company", "company_id"), _column("company", "company_name")],
        ),
        TableInfo(
            table_name="project",
            primary_key="project_id",
            columns=[_column("project", "project_id"), _column("project", "project_name")],
        ),
        TableInfo(
            table_name="bid_record",
            primary_key="bid_id",
            columns=[
                _column("bid_record", "bid_id"),
                _column("bid_record", "company_id"),
                _column("bid_record", "project_id"),
                _column("bid_record", "bid_amount", "numeric"),
            ],
        ),
    ]
    relationships = [
        RelationshipCandidate(
            source_table="bid_record",
            source_column="company_id",
            target_table="company",
            target_column="company_id",
            source="inferred_by_name_and_data",
            score=0.93,
            evidence={"data_coverage": 0.98},
            decision="auto_approved",
        ),
        RelationshipCandidate(
            source_table="bid_record",
            source_column="project_id",
            target_table="project",
            target_column="project_id",
            source="inferred_by_name_and_data",
            score=0.93,
            evidence={"data_coverage": 0.98},
            decision="auto_approved",
        ),
    ]
    return generate_mapping_from_schema(
        database_name="customer_db",
        tables=tables,
        relationships=relationships,
    )


def _build_sqlite_db(path: Path):
    with sqlite3.connect(path) as connection:
        connection.executescript(
            """
            CREATE TABLE company (
                company_id TEXT PRIMARY KEY,
                company_name TEXT
            );
            CREATE TABLE project (
                project_id TEXT PRIMARY KEY,
                project_name TEXT
            );
            CREATE TABLE bid_record (
                bid_id TEXT PRIMARY KEY,
                company_id TEXT,
                project_id TEXT,
                bid_amount NUMERIC
            );
            INSERT INTO company VALUES ('C001', 'Shenzhen Huaxin');
            INSERT INTO project VALUES ('P001', 'Guangxin Platform');
            INSERT INTO bid_record VALUES ('B001', 'C001', 'P001', 1000);
            """
        )


def test_generate_endpoint_writes_mapping_and_record(tmp_path, monkeypatch):
    from lightrag.kg_mapping import auto_generation_api

    def fake_generate(request):
        return {
            "generation_id": "gen_test",
            "mapping_path": str(tmp_path / "mappings" / "customer_db.gen_test.yaml"),
            "record_path": str(tmp_path / "records" / "gen_test.json"),
            "summary": {
                "tables": 3,
                "entities": 3,
                "relationships": 2,
                "auto_approved": 5,
                "need_review": 0,
                "blocked": 0,
            },
            "can_publish": True,
        }

    monkeypatch.setattr(auto_generation_api, "_generate_mapping_record", fake_generate)
    client = _build_client(tmp_path)

    response = client.post(
        "/audit/kg-mapping/generate",
        headers=_HEADERS,
        json={
            "connection_url": "postgresql://rag:rag@postgres:5432/customer_db",
            "schema": "public",
            "database_name": "customer_db",
            "workspace": "customer_workspace",
            "mode": "review_only",
        },
    )

    assert response.status_code == 200
    assert response.json()["generation_id"] == "gen_test"
    assert response.json()["can_publish"] is True


def test_generate_endpoint_merges_default_excluded_tables(tmp_path, monkeypatch):
    from lightrag.kg_mapping import auto_generation_api

    captured = {}

    def fake_generate(request):
        captured["excluded_tables"] = request.excluded_tables
        captured["excluded_table_patterns"] = request.excluded_table_patterns
        return {
            "generation_id": "gen_test",
            "mapping_path": str(tmp_path / "mappings" / "customer_db.gen_test.yaml"),
            "record_path": str(tmp_path / "records" / "gen_test.json"),
            "summary": {
                "tables": 0,
                "entities": 0,
                "relationships": 0,
                "auto_approved": 0,
                "need_review": 0,
                "blocked": 0,
            },
            "excluded_tables": ["audit_rule"],
            "can_publish": True,
        }

    monkeypatch.setattr(auto_generation_api, "_generate_mapping_record", fake_generate)
    client = _build_client(
        tmp_path,
        default_excluded_tables=["audit_rule"],
        default_excluded_table_patterns=["audit_rule*_backup_*"],
    )

    response = client.post(
        "/audit/kg-mapping/generate",
        headers=_HEADERS,
        json={
            "connection_url": "postgresql://rag:rag@postgres:5432/customer_db",
            "schema": "public",
            "database_name": "customer_db",
            "excluded_tables": ["project_alias"],
            "excluded_table_patterns": ["tmp_*"],
        },
    )

    assert response.status_code == 200
    assert captured["excluded_tables"] == ["audit_rule", "project_alias"]
    assert captured["excluded_table_patterns"] == [
        "audit_rule*_backup_*",
        "tmp_*",
    ]


def test_record_detail_preview_and_publish(tmp_path):
    db_path = tmp_path / "customer.db"
    _build_sqlite_db(db_path)
    sync_calls = []

    async def sync_callback(mapping, connection_url, workspace, apply, write_state, state):
        sync_calls.append(
            {
                "mapping": mapping,
                "connection_url": connection_url,
                "workspace": workspace,
                "apply": apply,
                "write_state": write_state,
                "state": state,
            }
        )
        return {"applied": apply, "workspace": workspace}

    client = _build_client(tmp_path, sync_callback=sync_callback)

    generate_response = client.post(
        "/audit/kg-mapping/generate",
        headers=_HEADERS,
        json={
            "connection_url": f"sqlite:///{db_path}",
            "schema": "public",
            "database_name": "customer_db",
            "workspace": "customer_workspace",
            "mode": "review_only",
            "prebuilt_mapping": _mapping(),
        },
    )
    assert generate_response.status_code == 200
    generation_id = generate_response.json()["generation_id"]

    detail_response = client.get(
        f"/audit/kg-mapping/generation/{generation_id}",
        headers=_HEADERS,
    )
    assert detail_response.status_code == 200
    assert detail_response.json()["generation_id"] == generation_id

    preview_response = client.post(
        f"/audit/kg-mapping/generation/{generation_id}/preview",
        headers=_HEADERS,
    )
    assert preview_response.status_code == 200
    preview = preview_response.json()
    assert preview["custom_kg"]["chunks"] > 0
    assert preview["custom_kg"]["entities"] > 0
    assert preview["custom_kg"]["relationships"] > 0
    assert any(
        "Shenzhen Huaxin" in entity["entity_name"]
        for entity in preview["sample_entities"]
    )

    publish_response = client.post(
        f"/audit/kg-mapping/generation/{generation_id}/publish",
        headers=_HEADERS,
        json={"apply": True, "write_state": True, "state": "/tmp/state.json"},
    )
    assert publish_response.status_code == 200
    assert publish_response.json()["applied"] is True
    assert sync_calls[0]["workspace"] == "customer_workspace"
    assert sync_calls[0]["apply"] is True
    current_path = tmp_path / "mappings" / "customer_db.current.yaml"
    assert current_path.exists()
    assert current_path.read_text(encoding="utf-8") == Path(
        generate_response.json()["mapping_path"]
    ).read_text(encoding="utf-8")

    detail_after_publish = client.get(
        f"/audit/kg-mapping/generation/{generation_id}",
        headers=_HEADERS,
    ).json()
    assert detail_after_publish["publish_status"] == "published"
    assert detail_after_publish["published_mapping_path"] == str(current_path)
    assert detail_after_publish["sync_result"]["applied"] is True


def test_generate_endpoint_uses_uploaded_relationship_metadata(tmp_path, monkeypatch):
    from lightrag.kg_mapping import auto_generation_api
    from lightrag.kg_mapping.auto_generator import TableInfo

    def fake_introspect(connection_url, *, schema, sample_limit):
        return (
            [
                TableInfo(
                    table_name="company",
                    primary_key="company_id",
                    columns=[
                        _column("company", "company_id"),
                        _column("company", "company_name"),
                    ],
                ),
                TableInfo(
                    table_name="bid_record",
                    primary_key="bid_id",
                    columns=[
                        _column("bid_record", "bid_id"),
                        _column("bid_record", "company_id"),
                    ],
                ),
            ],
            [],
            {},
        )

    monkeypatch.setattr(auto_generation_api, "introspect_postgres_schema", fake_introspect)
    client = _build_client(tmp_path)

    response = client.post(
        "/audit/kg-mapping/generate",
        headers=_HEADERS,
        data={
            "connection_url": "postgresql://rag:rag@postgres:5432/customer_db",
            "schema": "public",
            "database_name": "customer_db",
        },
        files={
            "metadata_files": (
                "relations.csv",
                (
                    "source_table,source_column,target_table,target_column\n"
                    "bid_record,company_id,company,company_id\n"
                ),
                "text/csv",
            )
        },
    )

    assert response.status_code == 200
    detail = client.get(
        f"/audit/kg-mapping/generation/{response.json()['generation_id']}",
        headers=_HEADERS,
    ).json()
    assert detail["input_sources"][0]["filename"] == "relations.csv"
    assert detail["input_sources"][0]["tables"] == 2
    assert detail["input_sources"][0]["table_names"] == ["bid_record", "company"]
    assert detail["metadata_table_counts"] == {
        "total": 2,
        "included": 2,
        "excluded": 0,
        "unmatched": 0,
        "table_names": ["bid_record", "company"],
        "included_table_names": ["bid_record", "company"],
        "excluded_table_names": [],
        "unmatched_table_names": [],
    }
    assert detail["relationships"][0]["source"] == "er_declared"
    assert detail["summary"]["relationships"] == 1


def test_generate_reports_metadata_table_counts_after_exclusions(
    tmp_path,
    monkeypatch,
):
    from lightrag.kg_mapping import auto_generation_api
    from lightrag.kg_mapping.auto_generator import TableInfo

    def fake_introspect(connection_url, *, schema, sample_limit):
        return (
            [
                TableInfo(
                    table_name="project",
                    primary_key="project_id",
                    columns=[
                        _column("project", "project_id"),
                        _column("project", "project_name"),
                    ],
                ),
                TableInfo(
                    table_name="project_alias",
                    primary_key="id",
                    columns=[
                        _column("project_alias", "id"),
                        _column("project_alias", "project_id"),
                    ],
                ),
            ],
            [],
            {},
        )

    monkeypatch.setattr(auto_generation_api, "introspect_postgres_schema", fake_introspect)
    client = _build_client(tmp_path, default_excluded_tables=["project_alias"])

    response = client.post(
        "/audit/kg-mapping/generate",
        headers=_HEADERS,
        data={
            "connection_url": "postgresql://rag:rag@postgres:5432/customer_db",
            "schema": "public",
            "database_name": "customer_db",
        },
        files={
            "metadata_files": (
                "relations.csv",
                (
                    "source_table,source_column,target_table,target_column\n"
                    "project_alias,project_id,project,project_id\n"
                ),
                "text/csv",
            )
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["input_sources"][0]["tables"] == 2
    assert body["metadata_table_counts"] == {
        "total": 2,
        "included": 1,
        "excluded": 1,
        "unmatched": 0,
        "table_names": ["project", "project_alias"],
        "included_table_names": ["project"],
        "excluded_table_names": ["project_alias"],
        "unmatched_table_names": [],
    }
    detail = client.get(
        f"/audit/kg-mapping/generation/{body['generation_id']}",
        headers=_HEADERS,
    ).json()
    assert detail["metadata_table_counts"] == body["metadata_table_counts"]


def test_generate_summary_reports_discovered_included_and_excluded_table_counts(
    tmp_path,
    monkeypatch,
):
    from lightrag.kg_mapping import auto_generation_api
    from lightrag.kg_mapping.auto_generator import TableInfo

    def fake_introspect(connection_url, *, schema, sample_limit):
        return (
            [
                TableInfo(
                    table_name="company",
                    primary_key="company_id",
                    columns=[
                        _column("company", "company_id"),
                        _column("company", "company_name"),
                    ],
                ),
                TableInfo(
                    table_name="project",
                    primary_key="project_id",
                    columns=[
                        _column("project", "project_id"),
                        _column("project", "project_name"),
                    ],
                ),
                TableInfo(
                    table_name="project_alias",
                    primary_key="id",
                    columns=[
                        _column("project_alias", "id"),
                        _column("project_alias", "project_id"),
                    ],
                ),
            ],
            [],
            {},
        )

    monkeypatch.setattr(auto_generation_api, "introspect_postgres_schema", fake_introspect)
    client = _build_client(tmp_path, default_excluded_tables=["project_alias"])

    response = client.post(
        "/audit/kg-mapping/generate",
        headers=_HEADERS,
        json={
            "connection_url": "postgresql://rag:rag@postgres:5432/customer_db",
            "schema": "public",
            "database_name": "customer_db",
            "mode": "review_only",
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["summary"]["tables"] == 2
    assert body["summary"]["included_tables"] == 2
    assert body["summary"]["discovered_tables"] == 3
    assert body["summary"]["excluded_tables"] == 1
    assert body["table_counts"] == {
        "discovered": 3,
        "included": 2,
        "excluded": 1,
    }
    detail = client.get(
        f"/audit/kg-mapping/generation/{body['generation_id']}",
        headers=_HEADERS,
    ).json()
    assert detail["excluded_tables"] == ["project_alias"]
    assert detail["table_counts"] == {
        "discovered": 3,
        "included": 2,
        "excluded": 1,
    }


def test_generate_endpoint_can_apply_llm_enhancement(tmp_path, monkeypatch):
    from lightrag.kg_mapping import auto_generation_api

    async def fake_llm_enhancer(payload):
        assert payload["database_name"] == "customer_db"
        assert any(entity["source"] == "bid_record" for entity in payload["entities"])
        return {
            "entity_labels": [{"entity_type": "BidRecord", "label": "投标记录"}],
            "relationships": [
                {
                    "source": "bid_record",
                    "old_relation_type": "BID_RECORD_COMPANY",
                    "relation_type": "SUBMITTED_BY",
                    "description_template": "{bid_id}由{company_name}提交。",
                }
            ],
        }

    client = _build_client(tmp_path, llm_enhancer=fake_llm_enhancer)
    response = client.post(
        "/audit/kg-mapping/generate",
        headers=_HEADERS,
        json={
            "connection_url": "postgresql://rag:rag@postgres:5432/customer_db",
            "schema": "public",
            "database_name": "customer_db",
            "workspace": "customer_workspace",
            "mode": "review_only",
            "prebuilt_mapping": _mapping(),
            "enable_llm_enhancement": True,
        },
    )

    assert response.status_code == 200
    assert "payload" not in response.json()["llm_enhancement"]
    assert response.json()["llm_enhancement"]["status"] == "applied"
    detail = client.get(
        f"/audit/kg-mapping/generation/{response.json()['generation_id']}",
        headers=_HEADERS,
    ).json()
    assert detail["llm_enhancement"]["enabled"] is True
    assert detail["llm_enhancement"]["trace"]["applied"]["entity_labels"] == 1
    assert detail["mapping"]["entity_types"]["BidRecord"]["label"] == "投标记录"
    assert any(
        relationship["relation_type"] == "SUBMITTED_BY"
        for relationship in detail["mapping"]["relationships"]
    )


def test_rollback_restores_previous_published_mapping(tmp_path):
    db_path = tmp_path / "customer.db"
    _build_sqlite_db(db_path)
    sync_calls = []

    async def sync_callback(mapping, connection_url, workspace, apply, write_state, state):
        sync_calls.append(
            {
                "mapping": mapping,
                "connection_url": connection_url,
                "workspace": workspace,
                "apply": apply,
                "write_state": write_state,
                "state": state,
            }
        )
        return {"applied": apply, "mapping": mapping}

    client = _build_client(tmp_path, sync_callback=sync_callback)

    first_response = client.post(
        "/audit/kg-mapping/generate",
        headers=_HEADERS,
        json={
            "connection_url": f"sqlite:///{db_path}",
            "schema": "public",
            "database_name": "customer_db",
            "workspace": "customer_workspace",
            "mode": "review_only",
            "prebuilt_mapping": _mapping(),
        },
    )
    first_id = first_response.json()["generation_id"]
    first_publish = client.post(
        f"/audit/kg-mapping/generation/{first_id}/publish",
        headers=_HEADERS,
        json={"apply": True, "write_state": True, "state": "/tmp/state-v1.json"},
    )
    assert first_publish.status_code == 200

    second_mapping = _mapping()
    second_mapping["sources"]["extra"] = {"table": "company", "primary_key": "company_id"}
    second_mapping["entities"].append(
        {
            "source": "extra",
            "entity_type": "Organization",
            "id_field": "company_id",
            "name_field": "company_name",
        }
    )
    second_response = client.post(
        "/audit/kg-mapping/generate",
        headers=_HEADERS,
        json={
            "connection_url": f"sqlite:///{db_path}",
            "schema": "public",
            "database_name": "customer_db",
            "workspace": "customer_workspace",
            "mode": "review_only",
            "prebuilt_mapping": second_mapping,
        },
    )
    second_id = second_response.json()["generation_id"]
    second_publish = client.post(
        f"/audit/kg-mapping/generation/{second_id}/publish",
        headers=_HEADERS,
        json={"apply": True, "write_state": True, "state": "/tmp/state-v2.json"},
    )
    assert second_publish.status_code == 200

    rollback = client.post(
        f"/audit/kg-mapping/generation/{second_id}/rollback",
        headers=_HEADERS,
        json={"apply": True, "write_state": True, "state": "/tmp/state-rollback.json"},
    )

    assert rollback.status_code == 200
    body = rollback.json()
    assert body["rolled_back_to_generation_id"] == first_id
    assert sync_calls[-1]["mapping"] == first_response.json()["mapping_path"]
    current_path = tmp_path / "mappings" / "customer_db.current.yaml"
    assert current_path.read_text(encoding="utf-8") == Path(
        first_response.json()["mapping_path"]
    ).read_text(encoding="utf-8")
    rolled_back_record = client.get(
        f"/audit/kg-mapping/generation/{second_id}",
        headers=_HEADERS,
    ).json()
    assert rolled_back_record["publish_status"] == "rolled_back"
    assert "published_mapping_path" not in rolled_back_record


def test_rollback_returns_clear_unsupported_response(tmp_path):
    client = _build_client(tmp_path)

    response = client.post(
        "/audit/kg-mapping/generation/gen_missing/rollback",
        headers=_HEADERS,
    )

    assert response.status_code == 404
