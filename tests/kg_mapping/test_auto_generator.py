from pathlib import Path
import sys
import types

import yaml
import pytest

from lightrag.kg_mapping.auto_generator import (
    ColumnInfo,
    RelationshipCandidate,
    TableInfo,
    apply_llm_mapping_enhancement,
    coverage_query,
    filter_schema_for_generation,
    generate_mapping_from_schema,
    infer_relationships,
    introspect_postgres_schema,
    load_generation_record,
    merge_mapping_configs,
    parse_relationship_metadata_file,
    quote_ident,
    save_generation_record,
    write_mapping_yaml,
)


pytestmark = pytest.mark.offline


def _column(table: str, name: str, data_type: str = "text") -> ColumnInfo:
    return ColumnInfo(table_name=table, column_name=name, data_type=data_type)


def _basic_tables() -> list[TableInfo]:
    return [
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


def test_generate_mapping_from_schema_creates_joined_event_mapping():
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

    mapping = generate_mapping_from_schema(
        database_name="customer_db",
        tables=_basic_tables(),
        relationships=relationships,
    )

    assert mapping["database_name"] == "customer_db"
    assert mapping["sources"]["company"]["table"] == "company"
    assert mapping["sources"]["project"]["table"] == "project"
    bid_query = mapping["sources"]["bid_record"]["query"]
    assert "LEFT JOIN company" in bid_query
    assert "LEFT JOIN project" in bid_query
    assert "c.company_name AS company_name" in bid_query
    assert "p.project_name AS project_name" in bid_query

    entity_types = {entity["entity_type"] for entity in mapping["entities"]}
    assert {"Organization", "Project", "BidRecord"} <= entity_types

    bid_entity = next(
        entity
        for entity in mapping["entities"]
        if entity["source"] == "bid_record"
    )
    assert bid_entity["entity_name_template"] == (
        "{company_name} bid_record {project_name} ({bid_id})"
    )
    assert "{company_name}" in bid_entity["description_template"]
    assert "{project_name}" in bid_entity["description_template"]

    relation_types = {
        relationship["relation_type"] for relationship in mapping["relationships"]
    }
    assert relation_types == {"BID_RECORD_COMPANY", "BID_RECORD_PROJECT"}


def test_relation_templates_use_join_output_names_for_generic_name_fields():
    tables = [
        TableInfo(
            table_name="person",
            primary_key="person_id",
            columns=[
                _column("person", "person_id"),
                _column("person", "name"),
            ],
        ),
        TableInfo(
            table_name="company",
            primary_key="company_id",
            columns=[
                _column("company", "company_id"),
                _column("company", "name"),
            ],
        ),
        TableInfo(
            table_name="person_company",
            primary_key="id",
            columns=[
                _column("person_company", "id"),
                _column("person_company", "person_id"),
                _column("person_company", "company_id"),
                _column("person_company", "status", "integer"),
            ],
        ),
    ]
    relationships = [
        RelationshipCandidate(
            source_table="person_company",
            source_column="person_id",
            target_table="person",
            target_column="person_id",
            source="inferred_by_name_and_data",
            score=0.93,
            evidence={"data_coverage": 0.98},
            decision="auto_approved",
        ),
        RelationshipCandidate(
            source_table="person_company",
            source_column="company_id",
            target_table="company",
            target_column="company_id",
            source="inferred_by_name_and_data",
            score=0.93,
            evidence={"data_coverage": 0.98},
            decision="auto_approved",
        ),
    ]

    mapping = generate_mapping_from_schema(
        database_name="customer_db",
        tables=tables,
        relationships=relationships,
    )

    query = mapping["sources"]["person_company"]["query"]
    assert "p.name AS person_name" in query
    assert "c.name AS company_name" in query
    descriptions = {
        relationship["description_template"]
        for relationship in mapping["relationships"]
    }
    assert any("{person_name}" in description for description in descriptions)
    assert any("{company_name}" in description for description in descriptions)
    assert not any("{name}" in description for description in descriptions)


def test_infer_relationships_uses_field_names_and_data_coverage():
    relationships = infer_relationships(
        _basic_tables(),
        coverage={
            ("bid_record", "company_id", "company", "company_id"): 0.98,
            ("bid_record", "project_id", "project", "project_id"): 0.96,
        },
    )

    by_target = {
        (item.source_table, item.source_column, item.target_table): item
        for item in relationships
    }

    company_relation = by_target[("bid_record", "company_id", "company")]
    assert company_relation.score >= 0.85
    assert company_relation.decision == "auto_approved"
    assert company_relation.evidence["data_coverage"] == 0.98

    project_relation = by_target[("bid_record", "project_id", "project")]
    assert project_relation.score >= 0.85
    assert project_relation.decision == "auto_approved"


def test_infer_relationships_does_not_auto_approve_unknown_id():
    tables = _basic_tables() + [
        TableInfo(
            table_name="unknown_record",
            primary_key="id",
            columns=[
                _column("unknown_record", "id"),
                _column("unknown_record", "missing_id"),
            ],
        )
    ]

    relationships = infer_relationships(tables)

    assert not any(
        item.source_table == "unknown_record" and item.source_column == "missing_id"
        for item in relationships
    )


def test_infer_relationships_preserves_explicit_relationships():
    explicit = RelationshipCandidate(
        source_table="bid_record",
        source_column="company_id",
        target_table="company",
        target_column="company_id",
        source="foreign_key",
        score=1.0,
        evidence={"constraint_name": "fk_bid_company"},
        decision="auto_approved",
    )

    relationships = infer_relationships(_basic_tables(), explicit_relationships=[explicit])

    assert explicit in relationships


def test_filter_schema_for_generation_excludes_tables_and_relationships():
    tables = _basic_tables() + [
        TableInfo(
            table_name="audit_rule",
            primary_key="id",
            columns=[_column("audit_rule", "id"), _column("audit_rule", "rule_name")],
        ),
        TableInfo(
            table_name="audit_rule_text_id_backup_20260625",
            primary_key="id",
            columns=[
                _column("audit_rule_text_id_backup_20260625", "id"),
                _column("audit_rule_text_id_backup_20260625", "rule_name"),
            ],
        ),
    ]
    relationships = [
        RelationshipCandidate(
            source_table="bid_record",
            source_column="company_id",
            target_table="company",
            target_column="company_id",
            source="foreign_key",
            score=1.0,
            evidence={},
            decision="auto_approved",
        ),
        RelationshipCandidate(
            source_table="audit_rule",
            source_column="project_id",
            target_table="project",
            target_column="project_id",
            source="foreign_key",
            score=1.0,
            evidence={},
            decision="auto_approved",
        ),
    ]
    coverage = {
        ("bid_record", "company_id", "company", "company_id"): 1.0,
        ("audit_rule", "project_id", "project", "project_id"): 1.0,
    }

    filtered_tables, filtered_relationships, filtered_coverage, excluded = (
        filter_schema_for_generation(
            tables,
            relationships,
            coverage,
            excluded_tables={"audit_rule"},
            excluded_table_patterns=["audit_rule_text_id_backup_*"],
        )
    )

    assert {table.table_name for table in filtered_tables} == {
        "company",
        "project",
        "bid_record",
    }
    assert filtered_relationships == [relationships[0]]
    assert filtered_coverage == {
        ("bid_record", "company_id", "company", "company_id"): 1.0
    }
    assert excluded == ["audit_rule", "audit_rule_text_id_backup_20260625"]


def test_parse_relationship_metadata_file_supports_structured_formats(tmp_path):
    json_relationships = parse_relationship_metadata_file(
        "relations.json",
        b"""
        {
          "relationships": [
            {
              "source_table": "bid_record",
              "source_column": "company_id",
              "target_table": "company",
              "target_column": "company_id"
            }
          ]
        }
        """,
    )
    yaml_relationships = parse_relationship_metadata_file(
        "relations.yaml",
        yaml.safe_dump(
            {
                "relationships": [
                    {
                        "source_table": "bid_record",
                        "source_column": "project_id",
                        "target_table": "project",
                        "target_column": "project_id",
                    }
                ]
            }
        ).encode("utf-8"),
    )
    csv_relationships = parse_relationship_metadata_file(
        "relations.csv",
        (
            "source_table,source_column,target_table,target_column\n"
            "person_enterprise_position,person_id,person,person_id\n"
        ).encode("utf-8"),
    )

    assert json_relationships[0].source == "er_declared"
    assert json_relationships[0].decision == "auto_approved"
    assert yaml_relationships[0].target_table == "project"
    assert csv_relationships[0].source_table == "person_enterprise_position"


def test_parse_relationship_metadata_file_supports_ddl_and_xlsx(tmp_path):
    ddl_relationships = parse_relationship_metadata_file(
        "schema.sql",
        b"""
        CREATE TABLE bid_record (
          bid_id text primary key,
          company_id text,
          project_id text,
          FOREIGN KEY (company_id) REFERENCES company(company_id)
        );
        ALTER TABLE bid_record ADD CONSTRAINT fk_bid_project
          FOREIGN KEY (project_id) REFERENCES project(project_id);
        """,
    )
    assert {
        (
            relationship.source_table,
            relationship.source_column,
            relationship.target_table,
            relationship.target_column,
        )
        for relationship in ddl_relationships
    } == {
        ("bid_record", "company_id", "company", "company_id"),
        ("bid_record", "project_id", "project", "project_id"),
    }

    openpyxl = pytest.importorskip("openpyxl")
    workbook = openpyxl.Workbook()
    sheet = workbook.active
    sheet.append(["source_table", "source_column", "target_table", "target_column"])
    sheet.append(["enterprise_certificate", "enterprise_id", "enterprise", "enterprise_id"])
    xlsx_path = tmp_path / "relations.xlsx"
    workbook.save(xlsx_path)

    xlsx_relationships = parse_relationship_metadata_file(
        "relations.xlsx",
        xlsx_path.read_bytes(),
    )

    assert len(xlsx_relationships) == 1
    assert xlsx_relationships[0].source_table == "enterprise_certificate"


def test_parse_relationship_metadata_file_supports_comment_relationship_hints():
    relationships = parse_relationship_metadata_file(
        "shareholding.sql",
        b"""
        CREATE TABLE enterprise_shareholding (
          id integer PRIMARY KEY,
          enterprise_name text,
          holder_type integer,
          holder_name text,
          shareholding_ratio numeric
        );

        -- enterprise_shareholding.enterprise_name -> enterprise.enterprise_name
        -- enterprise_shareholding.holder_name -> person.name when holder_type = 1
        -- enterprise_shareholding.holder_name -> enterprise.enterprise_name when holder_type = 2
        """,
    )

    assert {
        (
            relationship.source_table,
            relationship.source_column,
            relationship.target_table,
            relationship.target_column,
        )
        for relationship in relationships
    } == {
        (
            "enterprise_shareholding",
            "enterprise_name",
            "enterprise",
            "enterprise_name",
        ),
        ("enterprise_shareholding", "holder_name", "person", "name"),
        (
            "enterprise_shareholding",
            "holder_name",
            "enterprise",
            "enterprise_name",
        ),
    }
    assert all(relationship.source == "er_declared" for relationship in relationships)


def test_parse_relationship_metadata_file_supports_role_table_identity_ddl():
    ddl_relationships = parse_relationship_metadata_file(
        "audit-roles.sql",
        b"""
        CREATE TABLE project_person_role (
          id integer GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
          project_id text,
          person_id text,
          enterprise_id text
        );
        CREATE TABLE bid_person_role (
          id integer GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
          bid_id text,
          project_id text,
          enterprise_id text,
          person_id text
        );
        ALTER TABLE project_person_role ADD CONSTRAINT fk_project_person_role_project
          FOREIGN KEY (project_id) REFERENCES project(project_id);
        ALTER TABLE project_person_role ADD CONSTRAINT fk_project_person_role_person
          FOREIGN KEY (person_id) REFERENCES person(person_id);
        ALTER TABLE project_person_role ADD CONSTRAINT fk_project_person_role_enterprise
          FOREIGN KEY (enterprise_id) REFERENCES enterprise(enterprise_id);
        ALTER TABLE bid_person_role ADD CONSTRAINT fk_bid_person_role_bid
          FOREIGN KEY (bid_id) REFERENCES bid_record(bid_id);
        ALTER TABLE bid_person_role ADD CONSTRAINT fk_bid_person_role_project
          FOREIGN KEY (project_id) REFERENCES project(project_id);
        ALTER TABLE bid_person_role ADD CONSTRAINT fk_bid_person_role_enterprise
          FOREIGN KEY (enterprise_id) REFERENCES enterprise(enterprise_id);
        ALTER TABLE bid_person_role ADD CONSTRAINT fk_bid_person_role_person
          FOREIGN KEY (person_id) REFERENCES person(person_id);
        """,
    )

    assert {
        (
            relationship.source_table,
            relationship.source_column,
            relationship.target_table,
            relationship.target_column,
        )
        for relationship in ddl_relationships
    } == {
        ("project_person_role", "project_id", "project", "project_id"),
        ("project_person_role", "person_id", "person", "person_id"),
        ("project_person_role", "enterprise_id", "enterprise", "enterprise_id"),
        ("bid_person_role", "bid_id", "bid_record", "bid_id"),
        ("bid_person_role", "project_id", "project", "project_id"),
        ("bid_person_role", "enterprise_id", "enterprise", "enterprise_id"),
        ("bid_person_role", "person_id", "person", "person_id"),
    }


def test_merge_mapping_configs_preserves_existing_sources_for_incremental_updates():
    base = {
        "schema_version": "auto_kg_v1",
        "database_name": "customer_db",
        "sources": {
            "company": {"table": "company", "primary_key": "company_id"},
            "project": {"table": "project", "primary_key": "project_id"},
        },
        "entity_types": {
            "Organization": {"id_prefix": "Organization"},
            "Project": {"id_prefix": "Project"},
        },
        "entities": [
            {"source": "company", "entity_type": "Organization", "id_field": "company_id"},
            {"source": "project", "entity_type": "Project", "id_field": "project_id"},
        ],
        "relationships": [],
    }
    generated = {
        "schema_version": "auto_kg_v1",
        "database_name": "customer_db",
        "sources": {
            "company": {"table": "company", "primary_key": "company_id"},
            "bid_record": {"table": "bid_record", "primary_key": "bid_id"},
        },
        "entity_types": {
            "Organization": {"id_prefix": "Organization"},
            "BidRecord": {"id_prefix": "BidRecord"},
        },
        "entities": [
            {
                "source": "company",
                "entity_type": "Organization",
                "id_field": "company_id",
                "name_field": "company_name",
            },
            {"source": "bid_record", "entity_type": "BidRecord", "id_field": "bid_id"},
        ],
        "relationships": [
            {
                "source": "bid_record",
                "relation_type": "BID_RECORD_COMPANY",
                "src": {"entity_type": "BidRecord", "id_field": "bid_id"},
                "tgt": {"entity_type": "Organization", "id_field": "company_id"},
            }
        ],
    }

    merged = merge_mapping_configs(base, generated)

    assert set(merged["sources"]) == {"company", "project", "bid_record"}
    assert any(entity["source"] == "project" for entity in merged["entities"])
    assert any(entity["source"] == "bid_record" for entity in merged["entities"])
    company = next(entity for entity in merged["entities"] if entity["source"] == "company")
    assert company["name_field"] == "company_name"


def test_apply_llm_mapping_enhancement_only_updates_readability_fields():
    mapping = generate_mapping_from_schema(
        database_name="customer_db",
        tables=_basic_tables(),
        relationships=infer_relationships(
            _basic_tables(),
            coverage={
                ("bid_record", "company_id", "company", "company_id"): 0.98,
                ("bid_record", "project_id", "project", "project_id"): 0.96,
            },
        ),
    )
    original_source = mapping["sources"]["bid_record"].copy()

    enhanced, trace = apply_llm_mapping_enhancement(
        mapping,
        {
            "entity_labels": [
                {
                    "entity_type": "BidRecord",
                    "label": "投标记录",
                }
            ],
            "entities": [
                {
                    "source": "bid_record",
                    "entity_name_template": "{enterprise_name}参与{project_name}投标",
                    "description_template": "投标金额={bid_amount}; 排名={rank}",
                    "primary_key": "malicious_change",
                }
            ],
            "relationships": [
                {
                    "source": "bid_record",
                    "old_relation_type": "BID_RECORD_COMPANY",
                    "relation_type": "SUBMITTED_BY",
                    "description_template": "{bid_id}由{enterprise_name}提交。",
                    "src": {"id_field": "malicious_change"},
                }
            ],
        },
    )

    assert enhanced["sources"]["bid_record"] == original_source
    assert enhanced["entity_types"]["BidRecord"]["label"] == "投标记录"
    bid_entity = next(
        entity for entity in enhanced["entities"] if entity["source"] == "bid_record"
    )
    assert bid_entity["entity_name_template"] == "{enterprise_name}参与{project_name}投标"
    assert bid_entity["id_field"] == "bid_id"
    relation = next(
        relationship
        for relationship in enhanced["relationships"]
        if relationship["relation_type"] == "SUBMITTED_BY"
    )
    assert relation["src"]["id_field"] == "bid_id"
    assert trace["applied"]["entity_labels"] == 1
    assert trace["applied"]["entities"] == 1
    assert trace["applied"]["relationships"] == 1


def test_apply_llm_mapping_enhancement_accepts_common_llm_shapes():
    mapping = generate_mapping_from_schema(
        database_name="customer_db",
        tables=_basic_tables(),
        relationships=infer_relationships(
            _basic_tables(),
            coverage={
                ("bid_record", "company_id", "company", "company_id"): 0.98,
                ("bid_record", "project_id", "project", "project_id"): 0.96,
            },
        ),
    )

    enhanced, trace = apply_llm_mapping_enhancement(
        mapping,
        {
            "entity_labels": {
                "BidRecord": "投标记录",
                "Organization": "企业",
            },
            "relationships": [
                {
                    "source": "bid_record",
                    "relation_type": "BID_BY_COMPANY",
                    "description_template": "{bid_id}由{company_name}提交。",
                },
                {
                    "source": "bid_record",
                    "relation_type": "BID_FOR_PROJECT",
                    "description_template": "{bid_id}投向{project_name}。",
                },
            ],
        },
    )

    assert enhanced["entity_types"]["BidRecord"]["label"] == "投标记录"
    relation_types = [
        relationship["relation_type"]
        for relationship in enhanced["relationships"]
        if relationship["source"] == "bid_record"
    ]
    assert relation_types == ["BID_BY_COMPANY", "BID_FOR_PROJECT"]
    assert trace["applied"]["entity_labels"] == 2
    assert trace["applied"]["relationships"] == 2


def test_quote_ident_and_coverage_query_are_safe():
    assert quote_ident("project") == '"project"'
    assert quote_ident("project_name") == '"project_name"'
    with pytest.raises(ValueError):
        quote_ident("project;drop")

    query = coverage_query(
        "public",
        "bid_record",
        "project_id",
        "project",
        "project_id",
        500,
    )

    assert '"public"."bid_record"' in query
    assert '"public"."project"' in query
    assert "LIMIT 500" in query


def test_generation_record_and_mapping_files_round_trip(tmp_path):
    mapping = generate_mapping_from_schema(
        database_name="customer_db",
        tables=_basic_tables(),
        relationships=infer_relationships(
            _basic_tables(),
            coverage={
                ("bid_record", "company_id", "company", "company_id"): 0.98,
                ("bid_record", "project_id", "project", "project_id"): 0.96,
            },
        ),
    )
    mapping_path = write_mapping_yaml(
        tmp_path / "mappings",
        "customer_db",
        "gen_test",
        mapping,
    )
    record = {
        "generation_id": "gen_test",
        "database_name": "customer_db",
        "mapping_path": str(mapping_path),
        "summary": {"tables": 3},
        "mapping": mapping,
        "relationships": [],
    }

    record_path = save_generation_record(tmp_path / "records", record)
    loaded = load_generation_record(tmp_path / "records", "gen_test")

    assert record_path == Path(tmp_path / "records" / "gen_test.json")
    assert loaded["generation_id"] == "gen_test"
    assert loaded["mapping"]["database_name"] == "customer_db"
    assert "customer_db.gen_test.yaml" == mapping_path.name


def test_introspect_postgres_schema_uses_asyncpg_fallback(monkeypatch):
    from lightrag.kg_mapping import auto_generator

    class FakeRecord:
        def __init__(self, values):
            self._values = values

        def __getitem__(self, key):
            return self._values[key]

    class FakeConnection:
        def __init__(self):
            self.closed = False

        async def fetch(self, query, *args):
            if "FROM information_schema.columns" in query:
                assert args == ("public",)
                return [
                    FakeRecord(
                        {
                            "table_name": "company",
                            "column_name": "company_id",
                            "data_type": "text",
                            "is_nullable": "NO",
                            "table_comment": None,
                            "column_comment": None,
                            "primary_key": "company_id",
                        }
                    ),
                    FakeRecord(
                        {
                            "table_name": "company",
                            "column_name": "company_name",
                            "data_type": "text",
                            "is_nullable": "YES",
                            "table_comment": None,
                            "column_comment": None,
                            "primary_key": "company_id",
                        }
                    ),
                    FakeRecord(
                        {
                            "table_name": "bid_record",
                            "column_name": "bid_id",
                            "data_type": "text",
                            "is_nullable": "NO",
                            "table_comment": None,
                            "column_comment": None,
                            "primary_key": "bid_id",
                        }
                    ),
                    FakeRecord(
                        {
                            "table_name": "bid_record",
                            "column_name": "company_id",
                            "data_type": "text",
                            "is_nullable": "YES",
                            "table_comment": None,
                            "column_comment": None,
                            "primary_key": "bid_id",
                        }
                    ),
                ]
            if "tc.constraint_type = 'FOREIGN KEY'" in query:
                assert args == ("public",)
                return []
            raise AssertionError(f"Unexpected fetch query: {query}")

        async def fetchrow(self, query):
            assert '"public"."bid_record"' in query
            assert '"public"."company"' in query
            return FakeRecord({"total": 10, "matched": 9})

        async def close(self):
            self.closed = True

    async def fake_connect(connection_url):
        assert connection_url == "postgresql://rag:rag@postgres:5432/audit"
        return FakeConnection()

    monkeypatch.setattr(
        auto_generator,
        "_introspect_postgres_schema_with_psycopg",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("no psycopg")),
    )
    monkeypatch.setitem(
        sys.modules,
        "asyncpg",
        types.SimpleNamespace(connect=fake_connect),
    )

    tables, explicit, coverage = introspect_postgres_schema(
        "postgresql://rag:rag@postgres:5432/audit",
        schema="public",
        sample_limit=100,
    )

    assert [table.table_name for table in tables] == ["company", "bid_record"]
    assert explicit == []
    assert coverage[("bid_record", "company_id", "company", "company_id")] == 0.9
