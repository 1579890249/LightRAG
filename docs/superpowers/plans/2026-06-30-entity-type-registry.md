# Entity Type Registry Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a file-backed, workspace-scoped entity type registry with CRUD APIs and use it as the shared entity type source for document extraction and structured KG mapping.

**Architecture:** Add a small core registry module under `lightrag/entity_types.py`, a FastAPI router under `lightrag/api/routers/entity_type_routes.py`, and narrow integration points in `LightRAG._refresh_addon_params_cache()` plus audit KG sync. Structured mapping keeps its embedded `entity_types` metadata but is validated against active registry types when a registry is supplied.

**Tech Stack:** Python 3, FastAPI, Pydantic, PyYAML, pytest, existing `./scripts/test.sh` runner.

---

## File Structure

- Create `lightrag/entity_types.py`: owns registry schema, seed defaults, file path resolution, YAML load/save, guidance rendering, CRUD helpers, and mapping validation.
- Create `lightrag/api/routers/entity_type_routes.py`: exposes authenticated `GET/POST/PUT/DELETE /entity-types` routes.
- Modify `lightrag/api/lightrag_server.py`: include the new router.
- Modify `lightrag/lightrag.py`: when explicit `addon_params["entity_types_guidance"]` is absent, load workspace registry and inject registry-generated guidance into the cached prompt profile.
- Modify `lightrag/api/routers/audit_routes.py`: validate audit KG sync mappings against the current workspace registry before loading rows and building custom KG.
- Modify `lightrag/kg_mapping/config.py`: add optional `active_entity_types` validation without changing current default behavior.
- Test `tests/kg_mapping/test_entity_type_registry.py`: core registry behavior and mapping validation.
- Test `tests/api/routes/test_entity_type_routes.py`: CRUD API behavior.
- Modify `tests/extraction/test_entity_extraction_stability.py`: registry guidance default and explicit guidance override.
- Modify `tests/api/routes/test_audit_kg_sync_routes.py`: audit sync rejects inactive or unknown registry types.

---

### Task 1: Core Registry Module

**Files:**
- Create: `lightrag/entity_types.py`
- Test: `tests/kg_mapping/test_entity_type_registry.py`

- [ ] **Step 1: Write failing registry tests**

Add `tests/kg_mapping/test_entity_type_registry.py`:

```python
from pathlib import Path

import pytest

from lightrag.entity_types import (
    RESERVED_ENTITY_TYPES,
    create_entity_type,
    delete_entity_type,
    entity_type_registry_path,
    load_entity_type_registry,
    render_entity_types_guidance,
    update_entity_type,
    validate_mapping_entity_types,
)
from lightrag.kg_mapping.config import load_mapping_config


pytestmark = pytest.mark.offline


def test_missing_registry_is_seeded(tmp_path):
    registry = load_entity_type_registry("audit_customer_ys", base_dir=tmp_path)

    assert registry["schema_version"] == "entity_type_registry_v1"
    assert registry["workspace"] == "audit_customer_ys"
    assert registry["entity_types"]["Person"]["status"] == "active"
    assert registry["entity_types"]["Organization"]["status"] == "active"
    assert registry["entity_types"]["Other"]["status"] == "active"
    assert entity_type_registry_path("audit_customer_ys", tmp_path).exists()


def test_crud_helpers_create_update_and_soft_delete(tmp_path):
    created = create_entity_type(
        "audit_customer_ys",
        "PhoneNumber",
        label="电话号码",
        description="用于识别共用联系方式的电话号码。",
        base_dir=tmp_path,
    )

    assert created["entity_types"]["PhoneNumber"] == {
        "label": "电话号码",
        "description": "用于识别共用联系方式的电话号码。",
        "status": "active",
    }

    updated = update_entity_type(
        "audit_customer_ys",
        "PhoneNumber",
        label="联系电话",
        description="人员、组织或项目使用的联系电话。",
        status="inactive",
        base_dir=tmp_path,
    )
    assert updated["entity_types"]["PhoneNumber"]["label"] == "联系电话"
    assert updated["entity_types"]["PhoneNumber"]["status"] == "inactive"

    reactivated = create_entity_type(
        "audit_customer_ys",
        "PhoneNumber",
        label="电话号码",
        description="重新启用。",
        base_dir=tmp_path,
    )
    assert reactivated["entity_types"]["PhoneNumber"]["status"] == "active"
    assert reactivated["entity_types"]["PhoneNumber"]["description"] == "重新启用。"

    deleted = delete_entity_type(
        "audit_customer_ys",
        "PhoneNumber",
        base_dir=tmp_path,
    )
    assert deleted["entity_types"]["PhoneNumber"]["status"] == "inactive"


def test_reserved_other_cannot_be_deleted(tmp_path):
    assert "Other" in RESERVED_ENTITY_TYPES

    with pytest.raises(ValueError, match="reserved"):
        delete_entity_type("audit_customer_ys", "Other", base_dir=tmp_path)


def test_render_guidance_uses_only_active_types(tmp_path):
    create_entity_type(
        "audit_customer_ys",
        "PhoneNumber",
        label="电话号码",
        description="用于识别共用联系方式的电话号码。",
        base_dir=tmp_path,
    )
    update_entity_type(
        "audit_customer_ys",
        "Document",
        status="inactive",
        base_dir=tmp_path,
    )

    registry = load_entity_type_registry("audit_customer_ys", base_dir=tmp_path)
    guidance = render_entity_types_guidance(registry)

    assert "Classify each entity using one of the following approved types" in guidance
    assert "- PhoneNumber: 电话号码。用于识别共用联系方式的电话号码。" in guidance
    assert "- Document:" not in guidance
    assert "- Other:" in guidance


def test_validate_mapping_entity_types_rejects_unknown_and_inactive_types(tmp_path):
    update_entity_type(
        "audit_customer_ys",
        "Document",
        status="inactive",
        base_dir=tmp_path,
    )
    registry = load_entity_type_registry("audit_customer_ys", base_dir=tmp_path)
    mapping = load_mapping_config(
        {
            "schema_version": "audit_kg_v1",
            "database_name": "audit",
            "sources": {"doc": {"primary_key": "id"}},
            "entity_types": {
                "Document": {"id_prefix": "Document"},
                "UnknownType": {"id_prefix": "UnknownType"},
            },
            "entities": [
                {"source": "doc", "entity_type": "Document", "id_field": "id"},
            ],
            "relationships": [
                {
                    "source": "doc",
                    "relation_type": "DOC_TO_UNKNOWN",
                    "src": {"entity_type": "Document", "id_field": "id"},
                    "tgt": {"entity_type": "UnknownType", "id_field": "id"},
                }
            ],
        }
    )

    with pytest.raises(ValueError) as exc_info:
        validate_mapping_entity_types(mapping, registry)

    message = str(exc_info.value)
    assert "inactive entity types" in message
    assert "Document" in message
    assert "unknown entity types" in message
    assert "UnknownType" in message
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
./scripts/test.sh tests/kg_mapping/test_entity_type_registry.py
```

Expected: FAIL during import with `ModuleNotFoundError: No module named 'lightrag.entity_types'`.

- [ ] **Step 3: Implement registry module**

Create `lightrag/entity_types.py`:

```python
"""Workspace-scoped entity type registry utilities."""

from __future__ import annotations

import os
import re
import tempfile
from pathlib import Path
from typing import Any, Mapping

import yaml

from lightrag.kg_mapping.config import MappingConfig

SCHEMA_VERSION = "entity_type_registry_v1"
DEFAULT_ENTITY_TYPE_REGISTRY_DIR = "./data/entity_types"
ENTITY_TYPE_REGISTRY_DIR_ENV = "ENTITY_TYPE_REGISTRY_DIR"
RESERVED_ENTITY_TYPES = frozenset({"Other"})
_ENTITY_TYPE_NAME_RE = re.compile(r"^[A-Za-z][A-Za-z0-9_]*$")

DEFAULT_ENTITY_TYPES: dict[str, dict[str, str]] = {
    "Person": {
        "label": "人员",
        "description": "自然人、联系人、专家、项目人员等",
    },
    "Organization": {
        "label": "组织机构",
        "description": "企业、采购单位、代理机构、政府部门等",
    },
    "Project": {
        "label": "项目",
        "description": "招标项目、采购项目、工程项目等",
    },
    "BidSubmission": {
        "label": "投标记录",
        "description": "投标、报价、响应文件或供应商参与项目的记录",
    },
    "Document": {
        "label": "文档",
        "description": "招标文件、投标文件、公告、合同、报告等",
    },
    "Event": {
        "label": "事件",
        "description": "招标、投标、评审、签约、审批等业务事件",
    },
    "Certificate": {
        "label": "证书",
        "description": "企业或人员相关资质、证书、许可等",
    },
    "RevenueRecord": {
        "label": "营收记录",
        "description": "企业收入、业绩、财务或经营数据记录",
    },
    "ShareholdingRecord": {
        "label": "持股记录",
        "description": "自然人或企业持有企业股权的记录",
    },
    "PhoneNumber": {
        "label": "电话号码",
        "description": "用于识别人员、组织或项目共用联系方式的电话号码",
    },
    "EmailAddress": {
        "label": "电子邮箱",
        "description": "用于识别人员、组织或项目共用联系方式的邮箱地址",
    },
    "Identifier": {
        "label": "标识符",
        "description": "统一社会信用代码、身份证号、银行账号等高价值标识",
    },
    "Other": {
        "label": "其他",
        "description": "没有已批准类型适配时的兜底实体类型",
    },
}


def entity_type_registry_base_dir(base_dir: str | Path | None = None) -> Path:
    configured = (
        str(base_dir)
        if base_dir is not None
        else os.getenv(ENTITY_TYPE_REGISTRY_DIR_ENV, "").strip()
        or DEFAULT_ENTITY_TYPE_REGISTRY_DIR
    )
    return Path(configured).expanduser().resolve()


def entity_type_registry_path(workspace: str, base_dir: str | Path | None = None) -> Path:
    normalized = _normalize_workspace(workspace)
    return entity_type_registry_base_dir(base_dir) / f"{normalized}.yaml"


def load_entity_type_registry(
    workspace: str,
    *,
    base_dir: str | Path | None = None,
) -> dict[str, Any]:
    path = entity_type_registry_path(workspace, base_dir)
    if not path.exists():
        registry = _seed_registry(_normalize_workspace(workspace))
        save_entity_type_registry(registry, base_dir=base_dir)
        return registry

    with path.open("r", encoding="utf-8") as file:
        data = yaml.safe_load(file)
    return _normalize_registry(data, expected_workspace=_normalize_workspace(workspace))


def save_entity_type_registry(
    registry: Mapping[str, Any],
    *,
    base_dir: str | Path | None = None,
) -> dict[str, Any]:
    normalized = _normalize_registry(registry)
    path = entity_type_registry_path(str(normalized["workspace"]), base_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(
        prefix=f".{path.stem}.",
        suffix=".tmp",
        dir=str(path.parent),
        text=True,
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as file:
            yaml.safe_dump(normalized, file, allow_unicode=True, sort_keys=False)
        Path(temp_name).replace(path)
    except Exception:
        try:
            Path(temp_name).unlink(missing_ok=True)
        finally:
            raise
    return normalized


def create_entity_type(
    workspace: str,
    name: str,
    *,
    label: str = "",
    description: str = "",
    base_dir: str | Path | None = None,
) -> dict[str, Any]:
    registry = load_entity_type_registry(workspace, base_dir=base_dir)
    entity_types = registry["entity_types"]
    normalized_name = _normalize_entity_type_name(name)
    current = entity_types.get(normalized_name)
    if current and current.get("status") == "active":
        raise ValueError(f"Entity type '{normalized_name}' already exists")
    entity_types[normalized_name] = {
        "label": str(label or normalized_name).strip() or normalized_name,
        "description": str(description or "").strip(),
        "status": "active",
    }
    return save_entity_type_registry(registry, base_dir=base_dir)


def update_entity_type(
    workspace: str,
    name: str,
    *,
    label: str | None = None,
    description: str | None = None,
    status: str | None = None,
    base_dir: str | Path | None = None,
) -> dict[str, Any]:
    registry = load_entity_type_registry(workspace, base_dir=base_dir)
    normalized_name = _normalize_entity_type_name(name)
    entity_types = registry["entity_types"]
    if normalized_name not in entity_types:
        raise ValueError(f"Entity type '{normalized_name}' does not exist")
    current = dict(entity_types[normalized_name])
    if label is not None:
        current["label"] = str(label).strip() or normalized_name
    if description is not None:
        current["description"] = str(description).strip()
    if status is not None:
        current["status"] = _normalize_status(status)
    entity_types[normalized_name] = current
    return save_entity_type_registry(registry, base_dir=base_dir)


def delete_entity_type(
    workspace: str,
    name: str,
    *,
    base_dir: str | Path | None = None,
) -> dict[str, Any]:
    normalized_name = _normalize_entity_type_name(name)
    if normalized_name in RESERVED_ENTITY_TYPES:
        raise ValueError(f"Entity type '{normalized_name}' is reserved and cannot be deleted")
    return update_entity_type(
        workspace,
        normalized_name,
        status="inactive",
        base_dir=base_dir,
    )


def active_entity_type_names(registry: Mapping[str, Any]) -> set[str]:
    normalized = _normalize_registry(registry)
    return {
        name
        for name, config in normalized["entity_types"].items()
        if config.get("status") == "active"
    }


def render_entity_types_guidance(registry: Mapping[str, Any]) -> str:
    normalized = _normalize_registry(registry)
    lines = [
        "Classify each entity using one of the following approved types. If no type fits, use `Other`.",
        "",
    ]
    for name, config in normalized["entity_types"].items():
        if config.get("status") != "active":
            continue
        label = str(config.get("label") or name).strip()
        description = str(config.get("description") or "").strip()
        detail = f"{label}。{description}" if description else label
        lines.append(f"- {name}: {detail}")
    return "\n".join(lines).rstrip()


def validate_mapping_entity_types(
    mapping_config: MappingConfig,
    registry: Mapping[str, Any],
) -> None:
    normalized = _normalize_registry(registry)
    known = set(normalized["entity_types"])
    active = active_entity_type_names(normalized)
    referenced = set(mapping_config.entity_types)
    referenced.update(
        str(entity.get("entity_type"))
        for entity in mapping_config.entities
        if entity.get("entity_type")
    )
    for relationship in mapping_config.relationships:
        for endpoint_name in ("src", "tgt"):
            endpoint = relationship.get(endpoint_name)
            if isinstance(endpoint, Mapping) and endpoint.get("entity_type"):
                referenced.add(str(endpoint["entity_type"]))

    unknown = sorted(referenced - known)
    inactive = sorted((referenced & known) - active)
    problems = []
    if unknown:
        problems.append("unknown entity types: " + ", ".join(unknown))
    if inactive:
        problems.append("inactive entity types: " + ", ".join(inactive))
    if problems:
        raise ValueError("KG mapping references " + "; ".join(problems))


def _seed_registry(workspace: str) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "workspace": workspace,
        "entity_types": {
            name: {
                "label": config["label"],
                "description": config["description"],
                "status": "active",
            }
            for name, config in DEFAULT_ENTITY_TYPES.items()
        },
    }


def _normalize_registry(
    data: Any,
    *,
    expected_workspace: str | None = None,
) -> dict[str, Any]:
    if not isinstance(data, Mapping):
        raise ValueError("Entity type registry must be a YAML object")
    schema_version = str(data.get("schema_version") or "").strip()
    if schema_version != SCHEMA_VERSION:
        raise ValueError(
            f"Entity type registry schema_version must be '{SCHEMA_VERSION}'"
        )
    workspace = _normalize_workspace(str(data.get("workspace") or ""))
    if expected_workspace is not None and workspace != expected_workspace:
        raise ValueError(
            f"Entity type registry workspace '{workspace}' does not match '{expected_workspace}'"
        )
    raw_entity_types = data.get("entity_types")
    if not isinstance(raw_entity_types, Mapping) or not raw_entity_types:
        raise ValueError("Entity type registry field 'entity_types' must be a non-empty object")
    entity_types: dict[str, dict[str, str]] = {}
    for raw_name, raw_config in raw_entity_types.items():
        name = _normalize_entity_type_name(str(raw_name))
        if not isinstance(raw_config, Mapping):
            raise ValueError(f"Entity type '{name}' config must be an object")
        entity_types[name] = {
            "label": str(raw_config.get("label") or name).strip() or name,
            "description": str(raw_config.get("description") or "").strip(),
            "status": _normalize_status(str(raw_config.get("status") or "active")),
        }
    return {
        "schema_version": SCHEMA_VERSION,
        "workspace": workspace,
        "entity_types": entity_types,
    }


def _normalize_workspace(workspace: str) -> str:
    normalized = str(workspace or "default").strip() or "default"
    if "/" in normalized or "\\" in normalized or normalized in {".", ".."}:
        raise ValueError("workspace must be a simple name")
    return normalized


def _normalize_entity_type_name(name: str) -> str:
    normalized = str(name or "").strip()
    if not _ENTITY_TYPE_NAME_RE.match(normalized):
        raise ValueError(
            "Entity type name must start with a letter and contain only letters, digits, and underscores"
        )
    return normalized


def _normalize_status(status: str) -> str:
    normalized = str(status or "").strip().lower()
    if normalized not in {"active", "inactive"}:
        raise ValueError("Entity type status must be 'active' or 'inactive'")
    return normalized
```

- [ ] **Step 4: Run tests to verify they pass**

Run:

```bash
./scripts/test.sh tests/kg_mapping/test_entity_type_registry.py
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add lightrag/entity_types.py tests/kg_mapping/test_entity_type_registry.py
git commit -m "feat: add file-backed entity type registry"
```

---

### Task 2: Entity Type CRUD API

**Files:**
- Create: `lightrag/api/routers/entity_type_routes.py`
- Modify: `lightrag/api/lightrag_server.py`
- Test: `tests/api/routes/test_entity_type_routes.py`

- [ ] **Step 1: Write failing API tests**

Add `tests/api/routes/test_entity_type_routes.py`:

```python
from types import SimpleNamespace

import pytest
from fastapi import FastAPI, Header, HTTPException
from fastapi.testclient import TestClient

from lightrag.api.routers.entity_type_routes import create_entity_type_routes


pytestmark = pytest.mark.offline

_API_KEY = "test-key"
_HEADERS = {"X-API-Key": _API_KEY}


def _build_client(tmp_path):
    async def _auth(x_api_key: str | None = Header(default=None)):
        if x_api_key != _API_KEY:
            raise HTTPException(status_code=403, detail="Invalid API Key")

    app = FastAPI()
    rag = SimpleNamespace(workspace="audit_customer_ys")
    app.include_router(
        create_entity_type_routes(
            rag,
            auth_dependency=_auth,
            registry_base_dir=tmp_path,
        )
    )
    return TestClient(app)


def test_list_entity_types_seeds_registry(tmp_path):
    client = _build_client(tmp_path)

    response = client.get("/entity-types", headers=_HEADERS)

    assert response.status_code == 200
    payload = response.json()
    assert payload["workspace"] == "audit_customer_ys"
    names = [item["name"] for item in payload["entity_types"]]
    assert "Person" in names
    assert "Organization" in names
    assert "Other" in names


def test_create_update_and_delete_entity_type(tmp_path):
    client = _build_client(tmp_path)

    create_response = client.post(
        "/entity-types",
        headers=_HEADERS,
        json={
            "name": "PhoneNumber",
            "label": "电话号码",
            "description": "用于识别共用联系方式的电话号码。",
        },
    )
    assert create_response.status_code == 200
    assert create_response.json()["entity_type"]["status"] == "active"

    duplicate_response = client.post(
        "/entity-types",
        headers=_HEADERS,
        json={"name": "PhoneNumber", "label": "电话号码"},
    )
    assert duplicate_response.status_code == 409

    update_response = client.put(
        "/entity-types/PhoneNumber",
        headers=_HEADERS,
        json={
            "label": "联系电话",
            "description": "人员、组织或项目使用的联系电话。",
            "status": "active",
        },
    )
    assert update_response.status_code == 200
    assert update_response.json()["entity_type"]["label"] == "联系电话"

    delete_response = client.delete("/entity-types/PhoneNumber", headers=_HEADERS)
    assert delete_response.status_code == 200
    assert delete_response.json()["entity_type"]["status"] == "inactive"

    list_response = client.get("/entity-types", headers=_HEADERS)
    names = [item["name"] for item in list_response.json()["entity_types"]]
    assert "PhoneNumber" not in names

    list_inactive_response = client.get(
        "/entity-types?include_inactive=true",
        headers=_HEADERS,
    )
    inactive = {
        item["name"]: item
        for item in list_inactive_response.json()["entity_types"]
    }
    assert inactive["PhoneNumber"]["status"] == "inactive"


def test_delete_reserved_other_is_rejected(tmp_path):
    client = _build_client(tmp_path)

    response = client.delete("/entity-types/Other", headers=_HEADERS)

    assert response.status_code == 400
    assert "reserved" in response.json()["detail"]


def test_route_requires_auth(tmp_path):
    client = _build_client(tmp_path)

    response = client.get("/entity-types")

    assert response.status_code == 403
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
./scripts/test.sh tests/api/routes/test_entity_type_routes.py
```

Expected: FAIL during import with `ModuleNotFoundError` for `lightrag.api.routers.entity_type_routes`.

- [ ] **Step 3: Implement API router**

Create `lightrag/api/routers/entity_type_routes.py`:

```python
"""Entity type registry API routes."""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from lightrag.entity_types import (
    create_entity_type,
    delete_entity_type,
    load_entity_type_registry,
    update_entity_type,
)


class EntityTypeCreateRequest(BaseModel):
    name: str = Field(..., min_length=1)
    label: str = ""
    description: str = ""


class EntityTypeUpdateRequest(BaseModel):
    label: str | None = None
    description: str | None = None
    status: str | None = None


def create_entity_type_routes(
    rag,
    api_key: Optional[str] = None,
    auth_dependency=None,
    registry_base_dir: str | Path | None = None,
) -> APIRouter:
    router = APIRouter(tags=["entity-types"])
    if auth_dependency is None:
        from ..utils_api import get_combined_auth_dependency

        auth_dependency = get_combined_auth_dependency(api_key)

    def _workspace(workspace: str | None) -> str:
        return workspace or getattr(rag, "workspace", None) or "default"

    def _response(registry: dict, *, include_inactive: bool = True) -> dict:
        items = []
        for name, config in registry["entity_types"].items():
            if not include_inactive and config.get("status") != "active":
                continue
            items.append({"name": name, **config})
        return {
            "schema_version": registry["schema_version"],
            "workspace": registry["workspace"],
            "entity_types": items,
        }

    def _one(registry: dict, name: str) -> dict:
        config = registry["entity_types"][name]
        return {
            "workspace": registry["workspace"],
            "entity_type": {"name": name, **config},
        }

    @router.get("/entity-types", dependencies=[Depends(auth_dependency)])
    async def list_entity_types(
        workspace: str | None = Query(default=None),
        include_inactive: bool = Query(default=False),
    ):
        try:
            registry = load_entity_type_registry(
                _workspace(workspace),
                base_dir=registry_base_dir,
            )
            return _response(registry, include_inactive=include_inactive)
        except Exception as exc:
            raise HTTPException(status_code=500, detail=str(exc)) from exc

    @router.post("/entity-types", dependencies=[Depends(auth_dependency)])
    async def create_type(request: EntityTypeCreateRequest, workspace: str | None = None):
        try:
            registry = create_entity_type(
                _workspace(workspace),
                request.name,
                label=request.label,
                description=request.description,
                base_dir=registry_base_dir,
            )
            return _one(registry, request.name)
        except ValueError as exc:
            message = str(exc)
            status_code = 409 if "already exists" in message else 400
            raise HTTPException(status_code=status_code, detail=message) from exc

    @router.put("/entity-types/{name}", dependencies=[Depends(auth_dependency)])
    async def update_type(
        name: str,
        request: EntityTypeUpdateRequest,
        workspace: str | None = None,
    ):
        try:
            registry = update_entity_type(
                _workspace(workspace),
                name,
                label=request.label,
                description=request.description,
                status=request.status,
                base_dir=registry_base_dir,
            )
            return _one(registry, name)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @router.delete("/entity-types/{name}", dependencies=[Depends(auth_dependency)])
    async def delete_type(name: str, workspace: str | None = None):
        try:
            registry = delete_entity_type(
                _workspace(workspace),
                name,
                base_dir=registry_base_dir,
            )
            return _one(registry, name)
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    return router
```

Modify imports in `lightrag/api/lightrag_server.py`:

```python
from lightrag.api.routers.entity_type_routes import create_entity_type_routes
```

Add router inclusion next to graph/audit routes:

```python
app.include_router(create_entity_type_routes(rag, api_key))
```

- [ ] **Step 4: Run tests to verify they pass**

Run:

```bash
./scripts/test.sh tests/api/routes/test_entity_type_routes.py
```

Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add lightrag/api/routers/entity_type_routes.py lightrag/api/lightrag_server.py tests/api/routes/test_entity_type_routes.py
git commit -m "feat: add entity type registry api"
```

---

### Task 3: Document Extraction Uses Registry Guidance

**Files:**
- Modify: `lightrag/lightrag.py`
- Modify: `tests/extraction/test_entity_extraction_stability.py`

- [ ] **Step 1: Write failing extraction tests**

Append to `tests/extraction/test_entity_extraction_stability.py`:

```python

@pytest.mark.offline
def test_lightrag_uses_entity_type_registry_guidance_by_default(tmp_path):
    from lightrag import LightRAG
    from lightrag.entity_types import create_entity_type

    registry_dir = tmp_path / "entity_types"
    create_entity_type(
        "audit_customer_ys",
        "PhoneNumber",
        label="电话号码",
        description="用于识别共用联系方式的电话号码。",
        base_dir=registry_dir,
    )

    with patch.dict(os.environ, {"ENTITY_TYPE_REGISTRY_DIR": str(registry_dir)}):
        rag = LightRAG(
            working_dir=str(tmp_path / "rag"),
            workspace="audit_customer_ys",
            llm_model_func=AsyncMock(),
            embedding_func=_dummy_embedding_func(),
        )

    guidance = rag._entity_extraction_prompt_profile["entity_types_guidance"]
    assert "- PhoneNumber: 电话号码。用于识别共用联系方式的电话号码。" in guidance
    assert "Classify each entity using one of the following approved types" in guidance


@pytest.mark.offline
def test_lightrag_explicit_entity_types_guidance_overrides_registry(tmp_path):
    from lightrag import LightRAG
    from lightrag.entity_types import create_entity_type

    registry_dir = tmp_path / "entity_types"
    create_entity_type(
        "audit_customer_ys",
        "PhoneNumber",
        label="电话号码",
        description="用于识别共用联系方式的电话号码。",
        base_dir=registry_dir,
    )

    with patch.dict(os.environ, {"ENTITY_TYPE_REGISTRY_DIR": str(registry_dir)}):
        rag = LightRAG(
            working_dir=str(tmp_path / "rag"),
            workspace="audit_customer_ys",
            llm_model_func=AsyncMock(),
            embedding_func=_dummy_embedding_func(),
            addon_params={"entity_types_guidance": "- CustomOnly: explicit guidance"},
        )

    guidance = rag._entity_extraction_prompt_profile["entity_types_guidance"]
    assert guidance == "- CustomOnly: explicit guidance"
    assert "PhoneNumber" not in guidance
```

- [ ] **Step 2: Run tests to verify they fail**

Run:

```bash
./scripts/test.sh tests/extraction/test_entity_extraction_stability.py::test_lightrag_uses_entity_type_registry_guidance_by_default tests/extraction/test_entity_extraction_stability.py::test_lightrag_explicit_entity_types_guidance_overrides_registry
```

Expected: first test FAIL because `PhoneNumber` registry guidance is not injected into the prompt profile yet; second may pass or fail depending on implementation state, but must be kept as a regression guard.

- [ ] **Step 3: Integrate registry guidance in `LightRAG`**

Modify imports in `lightrag/lightrag.py`:

```python
from lightrag.entity_types import (
    load_entity_type_registry,
    render_entity_types_guidance,
)
```

Inside `_refresh_addon_params_cache()`, after `validate_entity_extraction_prompt_profile_for_mode(...)` assigns `self._entity_extraction_prompt_profile`, add:

```python
        if "entity_types_guidance" not in self._addon_params:
            registry = load_entity_type_registry(self.workspace or "default")
            self._entity_extraction_prompt_profile = {
                **self._entity_extraction_prompt_profile,
                "entity_types_guidance": render_entity_types_guidance(registry),
            }
```

Keep this after normal profile validation so prompt-file examples still work and explicit inline guidance still wins.

- [ ] **Step 4: Run tests to verify they pass**

Run:

```bash
./scripts/test.sh tests/extraction/test_entity_extraction_stability.py::test_lightrag_uses_entity_type_registry_guidance_by_default tests/extraction/test_entity_extraction_stability.py::test_lightrag_explicit_entity_types_guidance_overrides_registry
```

Expected: PASS.

- [ ] **Step 5: Run broader extraction stability tests**

Run:

```bash
./scripts/test.sh tests/extraction/test_entity_extraction_stability.py
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add lightrag/lightrag.py tests/extraction/test_entity_extraction_stability.py
git commit -m "feat: use entity type registry for extraction guidance"
```

---

### Task 4: Structured KG Mapping Registry Validation

**Files:**
- Modify: `lightrag/kg_mapping/config.py`
- Modify: `lightrag/api/routers/audit_routes.py`
- Modify: `tests/api/routes/test_audit_kg_sync_routes.py`

- [ ] **Step 1: Write failing audit sync validation test**

Append to `tests/api/routes/test_audit_kg_sync_routes.py`:

```python

def test_audit_kg_sync_rejects_mapping_with_inactive_registry_type(tmp_path, monkeypatch):
    from lightrag.entity_types import update_entity_type

    mapping_path = tmp_path / "mapping.yaml"
    db_path = tmp_path / "audit.db"
    state_path = tmp_path / "state.json"
    registry_dir = tmp_path / "entity_types"
    _write_mapping(mapping_path)
    _write_db(db_path)
    update_entity_type(
        "audit_customer_ys",
        "Organization",
        status="inactive",
        base_dir=registry_dir,
    )
    monkeypatch.setenv("ENTITY_TYPE_REGISTRY_DIR", str(registry_dir))

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
            "workspace": "audit_customer_ys",
        },
    )

    assert response.status_code == 400
    assert "inactive entity types" in response.json()["detail"]
    assert "Organization" in response.json()["detail"]
    rag.ainsert_custom_kg.assert_not_called()
```

- [ ] **Step 2: Run test to verify it fails**

Run:

```bash
./scripts/test.sh tests/api/routes/test_audit_kg_sync_routes.py::test_audit_kg_sync_rejects_mapping_with_inactive_registry_type
```

Expected: FAIL because audit sync currently succeeds without registry validation.

- [ ] **Step 3: Add optional mapping validation hook**

Modify `lightrag/kg_mapping/config.py` signature:

```python
def load_mapping_config(
    config: str | Path | dict[str, Any],
    *,
    active_entity_types: set[str] | None = None,
) -> MappingConfig:
```

After constructing `mapping_config`, before returning, add:

```python
    mapping_config = MappingConfig(
        schema_version=schema_version,
        database_name=database_name,
        sources=sources,
        entity_types=entity_types,
        entities=entities,
        relationships=relationships,
        raw=data,
    )
    if active_entity_types is not None:
        _validate_active_entity_types(mapping_config, active_entity_types)
    return mapping_config
```

Add helper:

```python
def _validate_active_entity_types(
    mapping_config: MappingConfig,
    active_entity_types: set[str],
) -> None:
    referenced = set(mapping_config.entity_types)
    referenced.update(
        str(entity.get("entity_type"))
        for entity in mapping_config.entities
        if entity.get("entity_type")
    )
    for relation_config in mapping_config.relationships:
        for endpoint_name in ("src", "tgt"):
            endpoint = relation_config.get(endpoint_name)
            if isinstance(endpoint, dict) and endpoint.get("entity_type"):
                referenced.add(str(endpoint["entity_type"]))
    unknown = sorted(referenced - active_entity_types)
    if unknown:
        raise ValueError(
            "KG mapping references entity types that are not active in the registry: "
            + ", ".join(unknown)
        )
```

This helper is intentionally optional so existing tests and callers keep their current behavior unless they pass a registry-derived set.

- [ ] **Step 4: Validate audit sync with full registry diagnostics**

Modify imports in `lightrag/api/routers/audit_routes.py`:

```python
from lightrag.entity_types import (
    load_entity_type_registry,
    validate_mapping_entity_types,
)
```

In `_run_audit_kg_sync`, wrap registry validation errors as `400` before starting the thread:

```python
    try:
        registry = load_entity_type_registry(workspace or "default")
        mapping_config_for_validation = load_mapping_config(request.mapping)
        validate_mapping_entity_types(mapping_config_for_validation, registry)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
```

Then keep `build_result = await asyncio.to_thread(_build_audit_kg_sync, request)` unchanged. This avoids passing registry state into the worker and gives clear API-level errors.

- [ ] **Step 5: Run test to verify it passes**

Run:

```bash
./scripts/test.sh tests/api/routes/test_audit_kg_sync_routes.py::test_audit_kg_sync_rejects_mapping_with_inactive_registry_type
```

Expected: PASS.

- [ ] **Step 6: Run broader audit route tests**

Run:

```bash
./scripts/test.sh tests/api/routes/test_audit_kg_sync_routes.py
```

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add lightrag/kg_mapping/config.py lightrag/api/routers/audit_routes.py tests/api/routes/test_audit_kg_sync_routes.py
git commit -m "feat: validate audit mappings against entity type registry"
```

---

### Task 5: Final Verification

**Files:**
- Verify all touched files.

- [ ] **Step 1: Run focused test suite**

Run:

```bash
./scripts/test.sh tests/kg_mapping/test_entity_type_registry.py tests/api/routes/test_entity_type_routes.py tests/extraction/test_entity_extraction_stability.py tests/api/routes/test_audit_kg_sync_routes.py
```

Expected: PASS for all selected tests.

- [ ] **Step 2: Run lint on touched Python files**

Run:

```bash
ruff check lightrag/entity_types.py lightrag/api/routers/entity_type_routes.py lightrag/lightrag.py lightrag/api/routers/audit_routes.py lightrag/kg_mapping/config.py tests/kg_mapping/test_entity_type_registry.py tests/api/routes/test_entity_type_routes.py tests/extraction/test_entity_extraction_stability.py tests/api/routes/test_audit_kg_sync_routes.py
```

Expected: PASS.

- [ ] **Step 3: Check git status**

Run:

```bash
git status --short
```

Expected: clean working tree, or only intentional uncommitted changes if the user asked not to commit implementation work.

- [ ] **Step 4: Report outcome**

Final response must include:

- files changed,
- tests run and pass/fail result,
- any follow-up deployment step such as adding an `/app/data/entity_types` mount if production compose does not already persist `/app/data`.

---

## Self-Review

- Spec coverage: storage, CRUD API, document extraction integration, structured mapping validation, errors/concurrency, and tests are each covered by tasks.
- Placeholder scan: no unfinished-marker phrases or vague validation-only steps remain; concrete test and implementation snippets are provided.
- Type consistency: route names, helper names, registry fields, and status values are consistent across tasks.
