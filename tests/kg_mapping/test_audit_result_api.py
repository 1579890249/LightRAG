import sqlite3

import pytest
from fastapi import FastAPI, Header, HTTPException
from fastapi.testclient import TestClient

from lightrag.kg_mapping.audit_result_api import create_audit_result_router


pytestmark = pytest.mark.offline

_API_KEY = "test-key"
_HEADERS = {"X-API-Key": _API_KEY}


def _build_db(path):
    with sqlite3.connect(path) as conn:
        conn.executescript(
            """
            CREATE TABLE audit_result (
                sno TEXT PRIMARY KEY,
                project_id TEXT NOT NULL,
                note TEXT NOT NULL,
                status INTEGER NOT NULL DEFAULT 0,
                remark TEXT
            );
            INSERT INTO audit_result VALUES
                ('20260623001', 'PJT004', 'Address similar to E003 legal person', 0, 'first'),
                ('20260623002', 'PJT004', 'Same address risk', 1, 'second'),
                ('20260623003', 'PJT005', 'Potential affiliation', 2, 'third');
            """
        )


def _build_client(db_path):
    async def _auth(x_api_key: str | None = Header(default=None)):
        if x_api_key != _API_KEY:
            raise HTTPException(status_code=403, detail="Invalid API Key")

    app = FastAPI()
    app.include_router(
        create_audit_result_router(
            connection_url=f"sqlite:///{db_path}",
            auth_dependency=_auth,
        )
    )
    return TestClient(app)


def test_audit_result_page_supports_project_and_status_filters(tmp_path):
    db_path = tmp_path / "audit.db"
    _build_db(db_path)
    client = _build_client(db_path)

    response = client.get(
        "/audit/results/page",
        headers=_HEADERS,
        params={
            "pageNum": 1,
            "pageSize": 30,
            "projectId": "PJT004",
            "status": 0,
        },
    )

    assert response.status_code == 200
    assert response.json() == {
        "total": 1,
        "rows": [
            {
                "sno": "20260623001",
                "project_id": "PJT004",
                "note": "Address similar to E003 legal person",
                "status": 0,
                "remark": "first",
            }
        ],
        "code": 200,
        "msg": "查询成功",
    }


def test_audit_result_crud_uses_post_for_add_update_and_get_for_delete(tmp_path):
    db_path = tmp_path / "audit.db"
    _build_db(db_path)
    client = _build_client(db_path)

    add_response = client.post(
        "/audit/results/add",
        headers=_HEADERS,
        json={
            "sno": "20260623004",
            "project_id": "PJT004",
            "note": "New audit finding",
            "status": 0,
            "remark": "created",
        },
    )
    assert add_response.status_code == 200
    assert add_response.json() == {
        "code": 200,
        "msg": "新增成功",
        "sno": "20260623004",
    }

    detail_response = client.get(
        "/audit/results/detail",
        headers=_HEADERS,
        params={"sno": "20260623004"},
    )
    assert detail_response.status_code == 200
    assert detail_response.json()["data"]["note"] == "New audit finding"

    update_response = client.post(
        "/audit/results/update",
        headers=_HEADERS,
        json={
            "sno": "20260623004",
            "note": "Updated audit finding",
            "status": 1,
            "remark": "updated",
        },
    )
    assert update_response.status_code == 200
    assert update_response.json() == {"code": 200, "msg": "修改成功"}

    updated_detail = client.get(
        "/audit/results/detail",
        headers=_HEADERS,
        params={"sno": "20260623004"},
    ).json()
    assert updated_detail["data"]["project_id"] == "PJT004"
    assert updated_detail["data"]["note"] == "Updated audit finding"
    assert updated_detail["data"]["status"] == 1
    assert updated_detail["data"]["remark"] == "updated"

    delete_response = client.get(
        "/audit/results/delete",
        headers=_HEADERS,
        params={"sno": "20260623004"},
    )
    assert delete_response.status_code == 200
    assert delete_response.json() == {"code": 200, "msg": "删除成功"}

    missing_detail = client.get(
        "/audit/results/detail",
        headers=_HEADERS,
        params={"sno": "20260623004"},
    )
    assert missing_detail.status_code == 404
    assert missing_detail.json() == {"code": 404, "msg": "审计结果不存在"}


def test_audit_result_add_generates_sno_when_not_provided(tmp_path):
    db_path = tmp_path / "audit.db"
    _build_db(db_path)
    client = _build_client(db_path)

    add_response = client.post(
        "/audit/results/add",
        headers=_HEADERS,
        json={
            "project_id": "PJT004",
            "note": "Generated sno finding",
            "status": 0,
            "remark": "generated",
        },
    )

    assert add_response.status_code == 200
    assert add_response.json() == {
        "code": 200,
        "msg": "新增成功",
        "sno": "20260623004",
    }

    detail_response = client.get(
        "/audit/results/detail",
        headers=_HEADERS,
        params={"sno": "20260623004"},
    )
    assert detail_response.status_code == 200
    assert detail_response.json()["data"]["note"] == "Generated sno finding"
