import json
import sqlite3
import subprocess
import sys


def test_audit_kg_sync_script_generates_payload_and_summary(tmp_path):
    db_path = tmp_path / "audit.db"
    output_path = tmp_path / "audit_kg.json"
    state_path = tmp_path / "state.json"

    with sqlite3.connect(db_path) as conn:
        conn.executescript(
            """
                CREATE TABLE enterprise (
                    enterprise_id TEXT PRIMARY KEY,
                    enterprise_name TEXT,
                    industry TEXT,
                    business_scope TEXT,
                    registered_address TEXT,
                    status TEXT
                );
                CREATE TABLE person (
                    person_id TEXT PRIMARY KEY,
                    name TEXT,
                    phone TEXT,
                    address TEXT,
                    is_expert INTEGER
                );
                CREATE TABLE project (
                    project_id TEXT PRIMARY KEY,
                    project_name TEXT,
                    tender_org TEXT,
                    tender_org_id TEXT,
                    budget NUMERIC,
                    bid_time TEXT,
                    status TEXT
                );
                CREATE TABLE bid_record (
                    bid_id TEXT PRIMARY KEY,
                    project_id TEXT,
                    enterprise_id TEXT,
                    bid_amount NUMERIC,
                    rank INTEGER
                );
                CREATE TABLE project_person_role (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    project_id TEXT,
                    person_id TEXT,
                    enterprise_id TEXT,
                    side TEXT,
                    role_type TEXT,
                    start_date TEXT,
                    end_date TEXT,
                    status TEXT,
                    remark TEXT
                );
                CREATE TABLE project_expert (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    project_id TEXT,
                    person_id TEXT,
                    role_type TEXT,
                    status TEXT,
                    remark TEXT
                );
                CREATE TABLE bid_person_role (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    bid_id TEXT,
                    project_id TEXT,
                    enterprise_id TEXT,
                    person_id TEXT,
                    role_type TEXT,
                    start_date TEXT,
                    end_date TEXT,
                    status TEXT,
                    remark TEXT
                );
                CREATE TABLE person_relation (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    person_id_1 TEXT,
                    person_id_2 TEXT,
                    relation_type TEXT
                );
                CREATE TABLE enterprise_certificate (
                    id TEXT PRIMARY KEY,
                    enterprise_id TEXT,
                    certificate_name TEXT,
                    certificate_type TEXT,
                    valid_start_date TEXT,
                    valid_end_date TEXT,
                    status TEXT
                );
                CREATE TABLE enterprise_revenue (
                    id TEXT PRIMARY KEY,
                    enterprise_id TEXT,
                    year INTEGER,
                    revenue_amount NUMERIC
                );
                CREATE TABLE person_enterprise_position (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    person_id TEXT,
                    enterprise_id TEXT,
                    position TEXT,
                    start_date TEXT,
                    end_date TEXT,
                    status INTEGER,
                    remark TEXT
                );
                CREATE TABLE enterprise_shareholding (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    enterprise_name TEXT,
                    holder_type INTEGER,
                    holder_name TEXT,
                    shareholding_ratio NUMERIC
                );
                INSERT INTO enterprise VALUES (
                    'E001', 'Huaxin', 'IT services',
                    'software development and system integration', 'Nanshan', 'active'
                );
                INSERT INTO enterprise VALUES (
                    'E002', 'Zhida', 'data services',
                    'data governance and audit analytics', 'Futian', 'active'
                );
                INSERT INTO person VALUES (
                    'P001', 'Zhang Wei', '13800138000', 'Nanshan', 0
                );
                INSERT INTO person VALUES (
                    'P009', 'Chen Gong', '', 'Futian', 1
                );
                INSERT INTO project VALUES (
                    'PJT001', 'Government Data Platform', 'Audit Bureau',
                    NULL, 12000000, '2026-06-01 09:30:00', 'done'
                );
                INSERT INTO bid_record VALUES ('B1', 'PJT001', 'E001', 10100000, 1);
                INSERT INTO person_relation (person_id_1, person_id_2, relation_type)
                VALUES ('P001', 'P009', 'relative');
                INSERT INTO enterprise_certificate VALUES (
                    'C001', 'E001', 'ISO 9001', 'quality management',
                    '2025-01-01', '2027-12-31', 'valid'
                );
                INSERT INTO enterprise_revenue VALUES (
                    'REV-E001-2025', 'E001', 2025, 50000000
                );
                INSERT INTO person_enterprise_position (
                    person_id, enterprise_id, position, start_date, end_date, status, remark
                ) VALUES (
                    'P009', 'E001', 'technical consultant',
                    '2025-03-01', NULL, 1, 'part-time appointment'
                );
                INSERT INTO enterprise_shareholding (
                    enterprise_name, holder_type, holder_name, shareholding_ratio
                ) VALUES (
                    'Huaxin', 1, 'Zhang Wei', 35.5
                );
                INSERT INTO enterprise_shareholding (
                    enterprise_name, holder_type, holder_name, shareholding_ratio
                ) VALUES (
                    'Huaxin', 2, 'Zhida', 15
                );
                """
            )

    command = [
        sys.executable,
        "scripts/audit_kg_sync.py",
        "--mapping",
        "configs/kg_mappings/audit_customer_ys.yaml",
        "--connection-url",
        f"sqlite:///{db_path}",
        "--state",
        str(state_path),
        "--output",
        str(output_path),
        "--write-state",
    ]
    completed = subprocess.run(
        command,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
    )

    summary = json.loads(completed.stdout)
    assert summary["sources"] == {
        "enterprise": 2,
        "person": 2,
        "project": 1,
        "bid_record": 1,
        "project_person_role": 0,
        "project_expert": 0,
        "bid_person_role": 0,
        "person_relation": 1,
        "enterprise_certificate": 1,
        "enterprise_revenue": 1,
        "person_enterprise_position": 1,
        "enterprise_shareholding": 2,
    }
    assert "audit_rule" not in summary["sources"]
    assert summary["custom_kg"] == {
        "chunks": 12,
        "entities": 10,
        "relationships": 10,
    }
    assert summary["sync_diff"]["insert"] == 12

    output = json.loads(output_path.read_text(encoding="utf-8"))
    entity_names = {item["entity_name"] for item in output["custom_kg"]["entities"]}
    assert "Huaxin" in entity_names
    assert "Zhida" in entity_names
    assert "Government Data Platform" in entity_names
    assert "Huaxin投标Government Data Platform（B1）" in entity_names
    assert "Zhang Wei持有Huaxin35.5%股权（1）" in entity_names
    assert "Zhida持有Huaxin15%股权（2）" in entity_names
    assert "Organization:E001" not in entity_names
    assert "Project:PJT001" not in entity_names
    assert "BidSubmission:B1" not in entity_names
    assert "Certificate:C001" in entity_names
    assert "RevenueRecord:REV-E001-2025" in entity_names
    descriptions = "\n".join(
        item["description"] for item in output["custom_kg"]["entities"]
    )
    assert "business_scope=software development and system integration" in descriptions
    assert "phone=13800138000" in descriptions
    assert "bid_time=2026-06-01 09:30:00" in descriptions
    assert "certificate_name=ISO 9001" in descriptions
    assert "revenue_amount=50000000" in descriptions
    assert "holder_type=1" in descriptions
    assert "shareholding_ratio=35.5" in descriptions
    bid_record_chunk = next(
        chunk
        for chunk in output["custom_kg"]["chunks"]
        if chunk["source_id"] == "db://audit/bid_record/B1"
    )
    assert "bid_id=B1" in bid_record_chunk["content"]
    assert "enterprise_id=E001" in bid_record_chunk["content"]
    assert "enterprise_name=Huaxin" in bid_record_chunk["content"]
    assert "project_id=PJT001" in bid_record_chunk["content"]
    assert "project_name=Government Data Platform" in bid_record_chunk["content"]

    relation_descriptions = "\n".join(
        item["description"] for item in output["custom_kg"]["relationships"]
    )
    relation_types = {item["keywords"] for item in output["custom_kg"]["relationships"]}
    assert "EMPLOYED_BY" not in relation_types
    assert "HOLDS_POSITION" in relation_types
    assert "confidence=" not in relation_descriptions
    assert "Zhang Wei 与 Chen Gong 存在人员关系：relative。" in relation_descriptions
    assert "Enterprise E001 has certificate C001." in relation_descriptions
    assert "Enterprise E001 has revenue record REV-E001-2025." in relation_descriptions
    assert (
        "Chen Gong holds position technical consultant at Huaxin "
        "from 2025-03-01 to ."
    ) in relation_descriptions
    assert "Zhang Wei holds 35.5% of Huaxin." in relation_descriptions
    assert "Zhida holds 15% of Huaxin." in relation_descriptions
    assert "NATURAL_PERSON_SHAREHOLDER" in relation_types
    assert "ENTERPRISE_SHAREHOLDER" in relation_types
    assert state_path.exists()
