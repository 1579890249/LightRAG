import sqlite3

import pytest

from lightrag.kg_mapping import (
    ConfigurableKGBuilder,
    ConfiguredSQLSource,
    load_mapping_config,
)


def test_configured_sql_source_loads_rows_for_mapping_sources(tmp_path):
    db_path = tmp_path / "audit.db"
    with sqlite3.connect(db_path) as conn:
        conn.executescript(
            """
            CREATE TABLE enterprise (
                enterprise_id TEXT PRIMARY KEY,
                enterprise_name TEXT
            );
            CREATE TABLE person (
                person_id TEXT PRIMARY KEY,
                name TEXT,
                enterprise_id TEXT
            );
            INSERT INTO enterprise VALUES ('E001', 'Huaxin');
            INSERT INTO person VALUES ('P001', 'Zhang Wei', 'E001');
            """
        )

    config = load_mapping_config(
        {
            "schema_version": "audit_kg_v1",
            "database_name": "audit",
            "sources": {
                "enterprise": {
                    "primary_key": "enterprise_id",
                    "query": "SELECT enterprise_id, enterprise_name FROM enterprise",
                },
                "person": {"primary_key": "person_id"},
            },
            "entity_types": {
                "Organization": {"id_prefix": "Organization"},
                "Person": {"id_prefix": "Person"},
            },
            "entities": [
                {
                    "source": "enterprise",
                    "entity_type": "Organization",
                    "id_field": "enterprise_id",
                    "name_field": "enterprise_name",
                },
                {
                    "source": "person",
                    "entity_type": "Person",
                    "id_field": "person_id",
                    "name_field": "name",
                },
            ],
        }
    )

    rows_by_source = ConfiguredSQLSource(f"sqlite:///{db_path}", config).load()
    custom_kg = ConfigurableKGBuilder(config).build(rows_by_source).custom_kg

    assert rows_by_source["enterprise"] == [
        {"enterprise_id": "E001", "enterprise_name": "Huaxin"}
    ]
    assert rows_by_source["person"] == [
        {"person_id": "P001", "name": "Zhang Wei", "enterprise_id": "E001"}
    ]
    assert {item["entity_name"] for item in custom_kg["entities"]} == {
        "Organization:E001",
        "Person:P001",
    }


def test_configured_sql_source_can_use_asyncpg_fallback(monkeypatch):
    class FakeConnection:
        async def fetch(self, query):
            assert query == 'SELECT * FROM "enterprise"'
            return [FakeRecord({"enterprise_id": "E001", "enterprise_name": "Huaxin"})]

        async def close(self):
            self.closed = True

    class FakeRecord:
        def __init__(self, values):
            self._values = values

        def keys(self):
            return self._values.keys()

        def __getitem__(self, key):
            return self._values[key]

    async def fake_connect(connection_url):
        assert connection_url == "postgresql://rag:rag@postgres:5432/audit"
        return FakeConnection()

    asyncpg = pytest.importorskip("asyncpg")
    monkeypatch.setattr(asyncpg, "connect", fake_connect)

    config = load_mapping_config(
        {
            "schema_version": "audit_kg_v1",
            "database_name": "audit",
            "sources": {
                "enterprise": {"primary_key": "enterprise_id"},
            },
            "entity_types": {
                "Organization": {"id_prefix": "Organization"},
            },
            "entities": [
                {
                    "source": "enterprise",
                    "entity_type": "Organization",
                    "id_field": "enterprise_id",
                    "name_field": "enterprise_name",
                },
            ],
        }
    )

    rows_by_source = ConfiguredSQLSource(
        "postgresql://rag:rag@postgres:5432/audit",
        config,
    ).load()

    assert rows_by_source == {
        "enterprise": [
            {"enterprise_id": "E001", "enterprise_name": "Huaxin"},
        ]
    }
