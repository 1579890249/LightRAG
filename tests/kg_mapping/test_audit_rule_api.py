import sqlite3

import pytest
from fastapi import FastAPI, Header, HTTPException
from fastapi.testclient import TestClient

from lightrag.kg_mapping.audit_rule_api import create_audit_rule_router


pytestmark = pytest.mark.offline

_API_KEY = "test-key"
_HEADERS = {"X-API-Key": _API_KEY}


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
                (1, 'Budget threshold', 'Article 1', '1', 'budget', 'first'),
                (2, 'Certificate required', 'Article 2', '1', 'qualification', 'second'),
                (3, 'Revenue review', 'Article 3', '0', 'finance', 'third');
            """
        )


def _build_client(db_path):
    async def _auth(x_api_key: str | None = Header(default=None)):
        if x_api_key != _API_KEY:
            raise HTTPException(status_code=403, detail="Invalid API Key")

    app = FastAPI()
    app.include_router(
        create_audit_rule_router(
            connection_url=f"sqlite:///{db_path}",
            auth_dependency=_auth,
        )
    )
    return TestClient(app)


def test_audit_rule_page_returns_total_rows_and_integer_ids(tmp_path):
    db_path = tmp_path / "audit.db"
    _build_db(db_path)
    client = _build_client(db_path)

    response = client.get(
        "/audit/rules/page",
        headers=_HEADERS,
        params={"pageNum": 1, "pageSize": 2},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 3
    assert body["code"] == 200
    assert body["rows"] == [
        {
            "id": 1,
            "rule_name": "Budget threshold",
            "rule_basis": "Article 1",
            "rule_status": "1",
            "rule_type": "budget",
            "remark": "first",
        },
        {
            "id": 2,
            "rule_name": "Certificate required",
            "rule_basis": "Article 2",
            "rule_status": "1",
            "rule_type": "qualification",
            "remark": "second",
        },
    ]


def test_audit_rule_crud_uses_integer_autoincrement_id(tmp_path):
    db_path = tmp_path / "audit.db"
    _build_db(db_path)
    client = _build_client(db_path)

    add_response = client.post(
        "/audit/rules/add",
        headers=_HEADERS,
        json={
            "rule_name": "Winning price review",
            "rule_basis": "Article 4",
            "rule_status": "1",
            "rule_type": "price",
            "remark": "created",
        },
    )
    assert add_response.status_code == 200
    assert add_response.json()["id"] == 4

    detail_response = client.get(
        "/audit/rules/detail",
        headers=_HEADERS,
        params={"id": 4},
    )
    assert detail_response.status_code == 200
    assert detail_response.json()["data"]["rule_name"] == "Winning price review"

    update_response = client.post(
        "/audit/rules/update",
        headers=_HEADERS,
        json={
            "id": 4,
            "rule_name": "Winning price review updated",
            "rule_status": "0",
            "remark": "updated",
        },
    )
    assert update_response.status_code == 200
    assert update_response.json()["code"] == 200

    updated_detail = client.get(
        "/audit/rules/detail",
        headers=_HEADERS,
        params={"id": 4},
    ).json()
    assert updated_detail["data"]["rule_name"] == "Winning price review updated"
    assert updated_detail["data"]["rule_basis"] == "Article 4"
    assert updated_detail["data"]["rule_status"] == "0"
    assert updated_detail["data"]["remark"] == "updated"

    delete_response = client.get(
        "/audit/rules/delete",
        headers=_HEADERS,
        params={"id": 4},
    )
    assert delete_response.status_code == 200
    assert delete_response.json()["code"] == 200

    missing_detail = client.get(
        "/audit/rules/detail",
        headers=_HEADERS,
        params={"id": 4},
    )
    assert missing_detail.status_code == 404


def test_audit_rule_rejects_string_id_for_update(tmp_path):
    db_path = tmp_path / "audit.db"
    _build_db(db_path)
    client = _build_client(db_path)

    response = client.post(
        "/audit/rules/update",
        headers=_HEADERS,
        json={"id": "RULE_RELATIVE_CHECK", "rule_name": "bad id"},
    )

    assert response.status_code == 422
