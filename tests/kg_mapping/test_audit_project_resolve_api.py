import sqlite3

import pytest
from fastapi import FastAPI, Header, HTTPException
from fastapi.testclient import TestClient

from lightrag.kg_mapping.audit_project_resolve_api import (
    create_audit_project_resolve_router,
)


pytestmark = pytest.mark.offline

_API_KEY = "test-key"
_HEADERS = {"X-API-Key": _API_KEY}


def _build_db(path):
    with sqlite3.connect(path) as conn:
        conn.executescript(
            """
            CREATE TABLE project (
                project_id TEXT PRIMARY KEY,
                project_name TEXT,
                tender_org TEXT,
                budget NUMERIC,
                status TEXT,
                bid_time TEXT
            );
            CREATE TABLE project_alias (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                project_id TEXT NOT NULL,
                alias_name TEXT NOT NULL,
                alias_type TEXT,
                remark TEXT
            );
            INSERT INTO project VALUES
                ('PJT004', '公共安全AI审计系统', '审计局', 20000000, '已完成', '2026-07-24'),
                ('PJT009', '人民医院门诊楼弱电改造项目', '人民医院', 8000000, '招标中', '2026-08-01'),
                ('PJT010', '人民医院住院楼改造项目', '人民医院', 12000000, '招标中', '2026-08-15');
            INSERT INTO project_alias (project_id, alias_name, alias_type, remark) VALUES
                ('PJT004', 'AI审计项目', 'short_name', NULL),
                ('PJT009', '医院弱电项目', 'short_name', NULL),
                ('PJT010', '医院改造项目', 'short_name', NULL);
            """
        )


def _build_client(db_path, config_path=None):
    async def _auth(x_api_key: str | None = Header(default=None)):
        if x_api_key != _API_KEY:
            raise HTTPException(status_code=403, detail="Invalid API Key")

    app = FastAPI()
    app.include_router(
        create_audit_project_resolve_router(
            connection_url=f"sqlite:///{db_path}",
            config_path=config_path or "missing-project-resolve.yaml",
            auth_dependency=_auth,
        )
    )
    return TestClient(app)


def test_project_resolve_auto_resolves_alias_match(tmp_path):
    db_path = tmp_path / "audit.db"
    _build_db(db_path)
    client = _build_client(db_path)

    response = client.post(
        "/audit/projects/resolve",
        headers=_HEADERS,
        json={"query": "查一下AI审计项目有没有围标风险", "limit": 5},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["code"] == 200
    assert body["resolved"] is True
    assert body["entity"]["project_id"] == "PJT004"
    assert body["entity"]["project_name"] == "公共安全AI审计系统"
    assert body["entity"]["matched_alias"] == "AI审计项目"
    assert body["entity"]["score"] >= 0.85
    assert body["candidates"][0]["project_id"] == "PJT004"


def test_project_resolve_returns_candidates_when_not_confident(tmp_path):
    db_path = tmp_path / "audit.db"
    _build_db(db_path)
    client = _build_client(db_path)

    response = client.post(
        "/audit/projects/resolve",
        headers=_HEADERS,
        json={
            "query": "医院项目关联风险",
            "limit": 5,
            "auto_resolve_score": 0.85,
            "auto_resolve_gap": 0.15,
        },
    )

    assert response.status_code == 200
    body = response.json()
    assert body["code"] == 200
    assert body["resolved"] is False
    candidate_ids = [candidate["project_id"] for candidate in body["candidates"]]
    assert "PJT009" in candidate_ids
    assert "PJT010" in candidate_ids
    assert body["msg"] == "找到多个相似项目，请确认要审查哪一个"


def test_project_resolve_supports_project_id_exact_match(tmp_path):
    db_path = tmp_path / "audit.db"
    _build_db(db_path)
    client = _build_client(db_path)

    response = client.post(
        "/audit/projects/resolve",
        headers=_HEADERS,
        json={"query": "PJT010", "limit": 5},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["resolved"] is True
    assert body["entity"]["project_id"] == "PJT010"
    assert body["entity"]["match_source"] == "project_id"


def test_project_resolve_stop_words_are_loaded_from_config(tmp_path):
    db_path = tmp_path / "audit.db"
    _build_db(db_path)
    config_path = tmp_path / "project_resolve.yaml"
    config_path.write_text("stop_words:\n  - 风险\n", encoding="utf-8")
    client = _build_client(db_path, config_path=config_path)

    response = client.post(
        "/audit/projects/resolve",
        headers=_HEADERS,
        json={"query": "AI审计项目风险", "limit": 5},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["resolved"] is True
    assert "风险" not in body["tokens"]
