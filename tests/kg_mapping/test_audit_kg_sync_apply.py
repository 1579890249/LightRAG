import json
import sqlite3
import sys
from pathlib import Path

import scripts.audit_kg_sync as audit_kg_sync
from lightrag.kg_mapping.apply import ApplyResult


def test_audit_kg_sync_apply_calls_lightrag_apply_layer(
    monkeypatch,
    tmp_path,
):
    db_path = tmp_path / "audit.db"
    output_path = tmp_path / "output.json"
    mapping_path = tmp_path / "mapping.yaml"
    mapping_path.write_text(
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
relationships: []
""",
        encoding="utf-8",
    )
    with sqlite3.connect(db_path) as conn:
        conn.executescript(
            """
            CREATE TABLE enterprise (
                enterprise_id TEXT PRIMARY KEY,
                enterprise_name TEXT
            );
            INSERT INTO enterprise VALUES ('E001', 'Huaxin');
            """
        )

    calls = []

    async def fake_apply(custom_kg, workspace=None):
        calls.append({"custom_kg": custom_kg, "workspace": workspace})
        return ApplyResult(
            inserted_chunks=len(custom_kg["chunks"]),
            inserted_entities=len(custom_kg["entities"]),
            inserted_relationships=len(custom_kg["relationships"]),
        )

    monkeypatch.setattr(audit_kg_sync, "apply_custom_kg", fake_apply)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "audit_kg_sync.py",
            "--mapping",
            str(mapping_path),
            "--connection-url",
            f"sqlite:///{db_path}",
            "--output",
            str(output_path),
            "--apply",
            "--workspace",
            "audit_customer_ys",
        ],
    )

    assert audit_kg_sync.main() == 0

    assert calls[0]["workspace"] == "audit_customer_ys"
    assert calls[0]["custom_kg"]["entities"][0]["entity_name"] == "Organization:E001"
    output = json.loads(Path(output_path).read_text(encoding="utf-8"))
    assert output["summary"]["applied"] is True
    assert output["summary"]["apply_result"] == {
        "inserted_chunks": 1,
        "inserted_entities": 1,
        "inserted_relationships": 0,
    }
