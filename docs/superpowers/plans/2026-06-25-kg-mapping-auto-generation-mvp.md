# KG Mapping Auto Generation MVP Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a first usable backend workflow that inspects a PostgreSQL customer database, generates a traceable `kg_mappings` YAML draft into the container-mounted data path, previews the generated graph, and publishes it through the existing KG sync pipeline without restarting LightRAG.

**Architecture:** Add a focused auto-generation module under `lightrag/kg_mapping` for schema introspection, relationship inference, mapping rendering, and generation record persistence. Expose customer-facing APIs from the existing audit router, keeping LightRAG core unchanged and reusing `load_mapping_config`, `ConfiguredSQLSource`, and `ConfigurableKGBuilder` for preview and publish validation.

**Tech Stack:** Python 3, FastAPI, Pydantic, PyYAML, PostgreSQL `information_schema`/`pg_catalog`, existing LightRAG custom KG APIs, pytest with SQLite/unit tests and mocked schema fixtures.

---

## MVP Scope

This first implementation should include:

- PostgreSQL schema introspection from an existing `connection_url`.
- Entity table detection.
- Relationship discovery from physical FK metadata plus field-name/data-coverage inference.
- Automatic joined source query generation for relation/event tables with only ID fields.
- YAML mapping draft generation.
- Generation record JSON saved under `/app/data/kg_mapping_generations` by default.
- Mapping files saved under `/app/data/kg_mappings` by default.
- `review_only`, `merge`, and `full_replace` request modes in the API contract, with MVP behavior treating `merge` and `full_replace` as new draft generation while preserving mode in the record.
- Preview by building `custom_kg` with existing builder without applying to LightRAG.
- Publish by calling the existing build/apply flow with the generated mapping path.

This first implementation should not include:

- ER Excel upload parsing.
- LLM-assisted semantics.
- Frontend pages.
- Deletion of old mappings during `full_replace`.

## File Structure

Create:

- `lightrag/kg_mapping/auto_generator.py`
  - Pure generation logic: schema model dataclasses, introspection wrappers, relationship scoring, table classification, join query generation, YAML dict rendering, generation record serialization.

- `lightrag/kg_mapping/auto_generation_api.py`
  - FastAPI router factory for generation, detail, preview, publish, and rollback. In MVP, rollback returns a clear unsupported response instead of mutating mappings.

- `tests/kg_mapping/test_auto_generator.py`
  - Unit tests for relationship inference, joined query generation, table classification, and mapping output.

- `tests/kg_mapping/test_auto_generation_api.py`
  - API tests using mocked generator/introspection or SQLite-compatible generated mappings where possible.

Modify:

- `lightrag/api/routers/audit_routes.py`
  - Include the auto-generation router and share the existing auth dependency.
  - Extract existing KG sync apply helper if needed so publish can reuse the same logic without duplicating large code.

- `lightrag/kg_mapping/__init__.py`
  - Export generation entry points if needed by tests or routers.

## Data Paths

Defaults:

```text
/app/data/kg_mappings
/app/data/kg_mapping_generations
```

Local/test overrides must be request parameters so tests can write into `tmp_path`.

## API Contract

### Generate

```http
POST /audit/kg-mapping/generate
```

Request:

```json
{
  "connection_url": "postgresql://rag:rag@postgres:5432/customer_db",
  "schema": "public",
  "database_name": "customer_db",
  "workspace": "customer_workspace",
  "mode": "merge",
  "business_domain": "generic",
  "mapping_dir": "/app/data/kg_mappings",
  "record_dir": "/app/data/kg_mapping_generations",
  "sample_limit": 500,
  "auto_approve_threshold": 0.85,
  "review_threshold": 0.65
}
```

Response:

```json
{
  "generation_id": "gen_20260625_001",
  "mapping_path": "/app/data/kg_mappings/customer_db.gen_20260625_001.yaml",
  "record_path": "/app/data/kg_mapping_generations/gen_20260625_001.json",
  "summary": {
    "tables": 6,
    "entities": 3,
    "relationships": 4,
    "auto_approved": 7,
    "need_review": 1,
    "blocked": 0
  },
  "can_publish": true
}
```

### Get Record

```http
GET /audit/kg-mapping/generation/{generation_id}
```

### Preview

```http
POST /audit/kg-mapping/generation/{generation_id}/preview
```

### Publish

```http
POST /audit/kg-mapping/generation/{generation_id}/publish
```

Request:

```json
{
  "apply": true,
  "write_state": true,
  "state": "/app/data/audit_kg_sync/customer_db_state.json"
}
```

### Rollback

```http
POST /audit/kg-mapping/generation/{generation_id}/rollback
```

MVP can return a clear `501` or `400` stating rollback metadata is recorded but automatic rollback is not implemented yet.

---

### Task 1: Define Auto-Generation Domain Model and Mapping Renderer

**Files:**
- Create: `lightrag/kg_mapping/auto_generator.py`
- Test: `tests/kg_mapping/test_auto_generator.py`

- [ ] **Step 1: Write tests for rendering a simple generated mapping**

Create `tests/kg_mapping/test_auto_generator.py` with a fixture that builds schema metadata for:

```text
company(company_id, company_name)
project(project_id, project_name)
bid_record(bid_id, company_id, project_id, bid_amount)
```

Assert generated YAML dict includes:

- `sources.company.table == "company"`
- `sources.project.table == "project"`
- `sources.bid_record.query` contains `LEFT JOIN company` and `LEFT JOIN project`
- `entities` include `Organization`, `Project`, and `BidSubmission`
- `BidSubmission.entity_name_template` uses `{company_name}` and `{project_name}`
- relationships include `BIDDER` and `FOR_PROJECT`

Run:

```bash
python -m pytest tests/kg_mapping/test_auto_generator.py -q
```

Expected: fail because `auto_generator.py` does not exist.

- [ ] **Step 2: Implement schema dataclasses and renderer**

In `auto_generator.py`, define dataclasses:

```python
@dataclass(frozen=True)
class ColumnInfo:
    table_name: str
    column_name: str
    data_type: str
    is_nullable: bool = True
    comment: str | None = None

@dataclass(frozen=True)
class TableInfo:
    table_name: str
    columns: list[ColumnInfo]
    primary_key: str | None = None
    comment: str | None = None
    row_count: int | None = None

@dataclass(frozen=True)
class RelationshipCandidate:
    source_table: str
    source_column: str
    target_table: str
    target_column: str
    source: str
    score: float
    evidence: dict[str, Any]
    decision: str
```

Define:

```python
def generate_mapping_from_schema(
    *,
    database_name: str,
    tables: list[TableInfo],
    relationships: list[RelationshipCandidate],
) -> dict[str, Any]:
    ...
```

Implement conservative heuristics:

- Table display field priority: `{table}_name`, `name`, `title`, `display_name`, first text column containing `name`.
- Entity type priority:
  - table contains `company` or `enterprise` -> `Organization`
  - table contains `project` -> `Project`
  - table contains `person` or `user` -> `Person`
  - otherwise PascalCase table name.
- Event table: table has two or more relationship candidates where it is source table and has fields like `amount`, `date`, `time`, `status`, `rank`, or table name contains `record`, `bid`, `contract`, `order`.
- For event table, create event entity and two relationships from event to target entities.
- Generate joined query for event table with aliases for target display fields.

- [ ] **Step 3: Run tests**

Run:

```bash
python -m pytest tests/kg_mapping/test_auto_generator.py -q
```

Expected: pass Task 1 tests.

---

### Task 2: Relationship Inference Without Foreign Keys

**Files:**
- Modify: `lightrag/kg_mapping/auto_generator.py`
- Test: `tests/kg_mapping/test_auto_generator.py`

- [ ] **Step 1: Add failing tests for inferred relation scoring**

Add tests for:

- `bid_record.company_id` infers `company.company_id` with score >= 0.85 when coverage is 0.98.
- `bid_record.unknown_id` does not auto-approve when no target table/column matches.
- ER/FK declared relationship gets source `foreign_key` or `er_declared` and auto-approved.

Use a pure function:

```python
def infer_relationships(
    tables: list[TableInfo],
    *,
    explicit_relationships: list[RelationshipCandidate] | None = None,
    coverage: dict[tuple[str, str, str, str], float] | None = None,
    auto_approve_threshold: float = 0.85,
    review_threshold: float = 0.65,
) -> list[RelationshipCandidate]:
    ...
```

Run tests and verify failure.

- [ ] **Step 2: Implement inference**

Implement field-name inference:

- `x_id` matches table `x` primary key.
- `x_id` matches target primary key named `x_id`.
- `enterprise_id` can match `enterprise.enterprise_id`.
- Same data type adds score.
- Coverage >= 0.95 adds score.

Set decisions:

```text
auto_approved
need_review
blocked
```

- [ ] **Step 3: Run tests**

Run:

```bash
python -m pytest tests/kg_mapping/test_auto_generator.py -q
```

Expected: pass.

---

### Task 3: PostgreSQL Introspection and Coverage Sampling

**Files:**
- Modify: `lightrag/kg_mapping/auto_generator.py`
- Test: `tests/kg_mapping/test_auto_generator.py`

- [ ] **Step 1: Add tests around SQL text builders**

Avoid live PostgreSQL in unit tests. Add tests for helper functions that produce safe metadata queries and coverage queries.

Expected helpers:

```python
def quote_ident(identifier: str) -> str: ...
def coverage_query(schema: str, source_table: str, source_column: str, target_table: str, target_column: str, sample_limit: int) -> str: ...
```

Assert identifiers are double-quoted and invalid identifiers raise `ValueError`.

- [ ] **Step 2: Implement PostgreSQL introspection**

Implement:

```python
def introspect_postgres_schema(
    connection_url: str,
    *,
    schema: str,
    sample_limit: int = 500,
) -> tuple[list[TableInfo], list[RelationshipCandidate], dict[tuple[str, str, str, str], float]]:
    ...
```

Use `psycopg` if installed, otherwise `asyncpg` fallback is acceptable. Read:

- tables from `information_schema.tables`
- columns from `information_schema.columns`
- PK/FK from `information_schema.table_constraints` and related views
- comments from `pg_catalog.obj_description` and `col_description`

Do not require integration tests in MVP.

- [ ] **Step 3: Run unit tests**

Run:

```bash
python -m pytest tests/kg_mapping/test_auto_generator.py -q
```

Expected: pass.

---

### Task 4: Generation Record Persistence

**Files:**
- Modify: `lightrag/kg_mapping/auto_generator.py`
- Test: `tests/kg_mapping/test_auto_generator.py`

- [ ] **Step 1: Add failing tests for saving and loading generation records**

Test that `save_generation_record(record_dir, record)` writes UTF-8 JSON and `load_generation_record(record_dir, generation_id)` returns it.

Record should include:

```python
{
  "generation_id": "gen_test",
  "database_name": "customer_db",
  "mapping_path": "...",
  "summary": {...},
  "mapping": {...},
  "relationships": [...],
}
```

- [ ] **Step 2: Implement persistence helpers**

Implement:

```python
def new_generation_id(now: datetime | None = None) -> str: ...
def save_generation_record(record_dir: str | Path, record: dict[str, Any]) -> Path: ...
def load_generation_record(record_dir: str | Path, generation_id: str) -> dict[str, Any]: ...
def write_mapping_yaml(mapping_dir: str | Path, database_name: str, generation_id: str, mapping: dict[str, Any]) -> Path: ...
```

Use `ensure_ascii=False` for JSON and UTF-8 for YAML.

- [ ] **Step 3: Run tests**

Run:

```bash
python -m pytest tests/kg_mapping/test_auto_generator.py -q
```

Expected: pass.

---

### Task 5: Auto Generation API Router

**Files:**
- Create: `lightrag/kg_mapping/auto_generation_api.py`
- Modify: `lightrag/api/routers/audit_routes.py`
- Test: `tests/kg_mapping/test_auto_generation_api.py`

- [ ] **Step 1: Write failing API tests**

Use FastAPI `TestClient` and monkeypatch generator functions so the test does not need PostgreSQL.

Test:

- `POST /audit/kg-mapping/generate` returns generation_id, mapping_path, summary, can_publish.
- `GET /audit/kg-mapping/generation/{id}` returns saved record.
- `POST /audit/kg-mapping/generation/{id}/preview` returns source/custom_kg counts from existing builder using saved mapping and a monkeypatched row loader.
- `POST /audit/kg-mapping/generation/{id}/publish` calls a supplied `sync_callback` or shared sync helper and returns applied summary.

- [ ] **Step 2: Implement router factory**

In `auto_generation_api.py`, implement:

```python
def create_kg_mapping_generation_router(
    *,
    auth_dependency=None,
    default_mapping_dir: str = "/app/data/kg_mappings",
    default_record_dir: str = "/app/data/kg_mapping_generations",
    sync_callback: Callable[[str, str, str | None, bool, bool], Awaitable[dict[str, Any]]] | None = None,
) -> APIRouter:
    ...
```

Request models:

- `KGMappingGenerateRequest`
- `KGMappingPreviewRequest`
- `KGMappingPublishRequest`

Keep route prefix exactly `/audit/kg-mapping` for now to stay with existing audit-specific APIs.

- [ ] **Step 3: Wire router into audit routes**

In `audit_routes.py`, include the router after existing audit CRUD routers. Provide a local `sync_callback` that constructs `AuditKGSyncRequest` using the generated mapping path and calls existing sync logic.

If direct reuse is awkward, extract a helper:

```python
async def _run_audit_kg_sync(rag, request: AuditKGSyncRequest) -> dict[str, Any]:
    ...
```

Then both `/audit/kg-sync` and publish route use it.

- [ ] **Step 4: Run tests**

Run:

```bash
python -m pytest tests/kg_mapping/test_auto_generation_api.py tests/kg_mapping/test_audit_rule_api.py -q
```

Expected: pass.

---

### Task 6: End-to-End Preview With Generated Mapping

**Files:**
- Modify: `tests/kg_mapping/test_auto_generation_api.py`
- Modify: `lightrag/kg_mapping/auto_generation_api.py`

- [ ] **Step 1: Add test using generated mapping against SQLite fixture**

Even though production introspection is PostgreSQL, preview can use existing `ConfiguredSQLSource` with SQLite. Create test tables:

```sql
CREATE TABLE company (company_id TEXT PRIMARY KEY, company_name TEXT);
CREATE TABLE project (project_id TEXT PRIMARY KEY, project_name TEXT);
CREATE TABLE bid_record (bid_id TEXT PRIMARY KEY, company_id TEXT, project_id TEXT, bid_amount NUMERIC);
```

Create a generation record whose mapping has joined queries compatible with SQLite. Call preview and assert:

- entities > 0
- relationships > 0
- chunks > 0
- sample entity name contains company or project name

- [ ] **Step 2: Implement preview response samples**

Preview response should include:

```json
{
  "sources": {"company": 1},
  "custom_kg": {"chunks": 3, "entities": 3, "relationships": 2},
  "sample_entities": [],
  "sample_relationships": [],
  "sample_chunks": []
}
```

Limit samples to 5 each.

- [ ] **Step 3: Run tests**

Run:

```bash
python -m pytest tests/kg_mapping/test_auto_generation_api.py tests/kg_mapping/test_auto_generator.py -q
```

Expected: pass.

---

### Task 7: Container Path and Compose Documentation

**Files:**
- Modify: `docs/kg-mapping-auto-generation-design.md`
- Modify: `docs/deployment/lightrag-server-172-16-1-203.md`

- [ ] **Step 1: Add operational note**

Document that generated files are written to:

```text
/app/data/kg_mappings
/app/data/kg_mapping_generations
```

and should be mounted from host paths:

```yaml
- ./data/kg_mappings:/app/data/kg_mappings
- ./data/kg_mapping_generations:/app/data/kg_mapping_generations
```

- [ ] **Step 2: Verify current compose has appropriate data mount or add note only**

Do not edit remote compose in this task unless the user asks. For local docs, note that future compose should mount these paths.

---

### Task 8: Verification

**Files:**
- No new files unless failures require fixes.

- [ ] **Step 1: Run focused test suite**

Run:

```bash
python -m pytest tests/kg_mapping/test_auto_generator.py tests/kg_mapping/test_auto_generation_api.py tests/kg_mapping/test_audit_rule_api.py -q
```

Expected: all pass.

- [ ] **Step 2: Run existing kg_mapping tests**

Run:

```bash
python -m pytest tests/kg_mapping -q
```

Expected: all pass. If unrelated existing tests fail due to environment, record the exact failure and run the relevant passing subset.

- [ ] **Step 3: Manual API smoke test on remote after deployment**

After copying code to remote/rebuilding or restarting service:

```bash
curl -sS -X POST http://127.0.0.1:9621/audit/kg-mapping/generate \
  -H 'Content-Type: application/json' \
  -d '{
    "connection_url":"postgresql://rag:rag@postgres:5432/audit",
    "schema":"public",
    "database_name":"audit",
    "workspace":"audit_customer_ys",
    "mode":"review_only"
  }'
```

Verify response has `generation_id`, `mapping_path`, `summary`, and `can_publish`.

Then call preview for that generation id and confirm nonzero `custom_kg` counts.
