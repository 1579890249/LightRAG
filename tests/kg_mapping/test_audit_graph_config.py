import sqlite3

import pytest

from lightrag.kg_mapping.audit_graph_config import (
    AuditGraphRuleConfigError,
    build_builtin_graph_config_for_rule_type,
    ensure_audit_rule_graph_config_table,
    load_active_graph_configs_for_rule_type,
    validate_graph_config,
)
from lightrag.kg_mapping.config import load_mapping_config


pytestmark = pytest.mark.offline


def _build_db(path):
    with sqlite3.connect(path) as conn:
        conn.executescript(
            """
            CREATE TABLE audit_rule (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                rule_name TEXT,
                rule_basis TEXT,
                rule_status TEXT,
                rule_type TEXT,
                remark TEXT
            );
            INSERT INTO audit_rule (
                id, rule_name, rule_basis, rule_status, rule_type, remark
            ) VALUES
                (
                    1,
                    'Bid relation risk',
                    'People and companies with tender/bid relationships should be checked.',
                    '1',
                    '招投标人际关系风险',
                    ''
                ),
                (
                    2,
                    'Disabled risk',
                    'Disabled rule should not be used.',
                    '0',
                    '招投标人际关系风险',
                    ''
                );
            """
        )


def _mapping_config():
    return load_mapping_config(
        {
            "schema_version": "audit_kg_v1",
            "database_name": "audit",
            "sources": {
                "person": {"primary_key": "person_id"},
                "enterprise": {"primary_key": "enterprise_id"},
                "person_relation": {"primary_key": "id"},
                "bid_person_role": {"primary_key": "id"},
            },
            "entity_types": {
                "Person": {"id_prefix": "Person"},
                "Organization": {"id_prefix": "Organization"},
                "BidSubmission": {"id_prefix": "BidSubmission"},
            },
            "entities": [
                {
                    "source": "person",
                    "entity_type": "Person",
                    "id_field": "person_id",
                },
                {
                    "source": "enterprise",
                    "entity_type": "Organization",
                    "id_field": "enterprise_id",
                },
            ],
            "relationships": [
                {
                    "source": "person_relation",
                    "relation_type": "PERSON_RELATED",
                    "src": {"entity_type": "Person", "id_field": "person_id_1"},
                    "tgt": {"entity_type": "Person", "id_field": "person_id_2"},
                },
                {
                    "source": "person",
                    "relation_type": "HOLDS_POSITION",
                    "src": {"entity_type": "Person", "id_field": "person_id"},
                    "tgt": {
                        "entity_type": "Organization",
                        "id_field": "enterprise_id",
                    },
                },
                {
                    "source": "bid_person_role",
                    "relation_type": "BID_PERSON_ROLE",
                    "src": {"entity_type": "BidSubmission", "id_field": "bid_id"},
                    "tgt": {"entity_type": "Person", "id_field": "person_id"},
                },
                {
                    "source": "bid_person_role",
                    "relation_type": "BID_ROLE_ORG",
                    "src": {"entity_type": "Person", "id_field": "person_id"},
                    "tgt": {
                        "entity_type": "Organization",
                        "id_field": "enterprise_id",
                    },
                },
            ],
        }
    )


def test_load_active_graph_configs_creates_hidden_table_and_returns_valid_config(tmp_path):
    db_path = tmp_path / "audit.db"
    _build_db(db_path)
    connection_url = f"sqlite:///{db_path}"

    ensure_audit_rule_graph_config_table(connection_url)
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            """
            INSERT INTO audit_rule_graph_config (
                rule_id, config, basis_hash, status
            ) VALUES (
                1,
                '{"allowed_entity_types":["Person","Organization"],"relation_types":["PERSON_RELATED"],"default_max_depth":4}',
                'basis-hash',
                'active'
            )
            """
        )
        conn.commit()

    configs = load_active_graph_configs_for_rule_type(
        connection_url,
        "招投标人际关系风险",
        _mapping_config(),
    )

    assert len(configs) == 1
    assert configs[0]["rule_id"] == 1
    assert configs[0]["rule_name"] == "Bid relation risk"
    assert configs[0]["rule_basis"].startswith("People and companies")
    assert configs[0]["config"] == {
        "allowed_entity_types": ["Person", "Organization"],
        "relation_types": ["PERSON_RELATED"],
        "default_max_depth": 4,
    }


def test_load_active_graph_configs_accepts_chinese_enabled_status(tmp_path):
    db_path = tmp_path / "audit.db"
    _build_db(db_path)
    connection_url = f"sqlite:///{db_path}"

    ensure_audit_rule_graph_config_table(connection_url)
    with sqlite3.connect(db_path) as conn:
        conn.execute("UPDATE audit_rule SET rule_status = '启用' WHERE id = 1")
        conn.execute(
            """
            INSERT INTO audit_rule_graph_config (
                rule_id, config, basis_hash, status
            ) VALUES (
                1,
                '{"allowed_entity_types":["Person"],"relation_types":["PERSON_RELATED"],"default_max_depth":3}',
                'basis-hash',
                'active'
            )
            """
        )
        conn.commit()

    configs = load_active_graph_configs_for_rule_type(
        connection_url,
        "招投标人际关系风险",
        _mapping_config(),
    )

    assert len(configs) == 1
    assert configs[0]["rule_status"] == "启用"


def test_validate_graph_config_rejects_unknown_relation_type():
    with pytest.raises(AuditGraphRuleConfigError) as exc_info:
        validate_graph_config(
            {
                "allowed_entity_types": ["Person"],
                "relation_types": ["UNKNOWN_RELATION"],
                "default_max_depth": 4,
            },
            _mapping_config(),
        )

    assert "UNKNOWN_RELATION" in str(exc_info.value)


def test_validate_graph_config_rejects_unknown_entity_type():
    with pytest.raises(AuditGraphRuleConfigError) as exc_info:
        validate_graph_config(
            {
                "allowed_entity_types": ["Company"],
                "relation_types": ["PERSON_RELATED"],
                "default_max_depth": 4,
            },
            _mapping_config(),
        )

    assert "Company" in str(exc_info.value)


def test_build_builtin_graph_config_filters_template_to_current_mapping():
    config = build_builtin_graph_config_for_rule_type(
        "人际关系",
        _mapping_config(),
    )

    assert config == {
        "allowed_entity_types": ["Person", "Organization", "BidSubmission"],
        "relation_types": [
            "PERSON_RELATED",
            "HOLDS_POSITION",
            "BID_PERSON_ROLE",
            "BID_ROLE_ORG",
        ],
        "default_max_depth": 4,
    }


def test_build_equity_graph_config_includes_bid_person_role_edges():
    config = build_builtin_graph_config_for_rule_type(
        "股权关系",
        _mapping_config(),
    )

    assert "BID_PERSON_ROLE" in config["relation_types"]
    assert "BID_ROLE_ORG" in config["relation_types"]
