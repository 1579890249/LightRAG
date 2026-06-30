# Dynamic Entity Type Registry Design

## Goal

Make LightRAG entity types dynamically configurable through a file-backed registry, expose CRUD APIs for operators, and use the same approved entity type set for document extraction and structured database KG mapping.

## Scope

This first phase uses YAML files under a runtime data directory. It does not add a UI, PostgreSQL tables, an approval workflow, or automatic migration of existing graph node `entity_type` values.

## Registry Storage

The registry is workspace-scoped and stored as YAML:

```text
/app/data/entity_types/{workspace}.yaml
```

For local tests and non-container runs, the base directory is configurable and defaults to:

```text
./data/entity_types
```

The file shape is:

```yaml
schema_version: entity_type_registry_v1
workspace: audit_customer_ys
entity_types:
  Person:
    label: 人员
    description: 自然人、联系人、专家、项目人员等
    status: active
  Organization:
    label: 组织机构
    description: 企业、采购单位、代理机构、政府部门等
    status: active
```

Entity type names are stable identifiers. They must be non-empty ASCII-like symbolic names suitable for prompt and mapping references, for example `Person`, `Organization`, `BidSubmission`, `PhoneNumber`, and `ShareholdingRecord`. Labels and descriptions can be Chinese.

The system initializes a missing registry from the current canonical seed:

- `Person`
- `Organization`
- `Project`
- `BidSubmission`
- `Document`
- `Event`
- `Certificate`
- `RevenueRecord`
- `ShareholdingRecord`
- `PhoneNumber`
- `EmailAddress`
- `Identifier`
- `Other`

`Other` is reserved for document extraction fallback and cannot be deleted.

## CRUD API

Add a general authenticated router, not audit-specific:

```text
GET    /entity-types
POST   /entity-types
PUT    /entity-types/{name}
DELETE /entity-types/{name}
```

All routes use the existing combined auth dependency.

`GET /entity-types` returns the registry for the current workspace. Query parameters:

- `workspace`: optional, defaults to current `rag.workspace`.
- `include_inactive`: optional boolean, defaults to `false`.

`POST /entity-types` creates an active type. Request body:

```json
{
  "name": "PhoneNumber",
  "label": "电话号码",
  "description": "用于识别人员、组织或项目共用联系方式的电话号码。"
}
```

It returns `409` if the active type already exists. If the type exists as inactive, creation reactivates it and updates label/description.

`PUT /entity-types/{name}` updates label, description, and status. Renaming is intentionally excluded in phase 1 because mappings and existing graph nodes reference the name.

`DELETE /entity-types/{name}` soft-deletes by setting `status: inactive`. It rejects deletion of `Other`. It does not rewrite existing mappings or graph nodes.

## Document Extraction Integration

Document extraction currently receives `entity_types_guidance` through `addon_params` / prompt profiles. The new registry becomes the default source of guidance:

1. `LightRAG._refresh_addon_params_cache()` resolves the normal prompt profile.
2. If explicit `addon_params["entity_types_guidance"]` is present, it still wins for compatibility.
3. Otherwise, the registry is loaded for the current workspace and converted into guidance lines.
4. The resolved `_entity_extraction_prompt_profile["entity_types_guidance"]` is updated with those lines.

Generated guidance format:

```text
Classify each entity using one of the following approved types. If no type fits, use `Other`.

- Person: 人员。自然人、联系人、专家、项目人员等
- Organization: 组织机构。企业、采购单位、代理机构、政府部门等
```

This keeps the existing extraction prompt contract unchanged while making the approved type list dynamic.

## Structured KG Mapping Integration

Structured database KG mapping currently validates against `entity_types` embedded in mapping YAML. Phase 1 keeps that field for mapping readability and per-type metadata such as `id_prefix`, but validates the referenced entity type names against the shared registry when a registry is supplied.

Audit KG sync and audit mapping generation/publish paths will load the workspace registry and validate:

- every mapping `entity_types` key is active in the registry,
- every `entities[].entity_type` is active in the registry,
- every relationship endpoint `entity_type` is active in the registry.

Unknown or inactive types return `400` with a clear message. This keeps document extraction and structured ingestion on one approved type system.

## Errors And Concurrency

Registry reads tolerate a missing file by creating the seed registry. Invalid YAML or invalid schema returns a server error for direct API reads and a clear validation error for KG sync.

Writes use atomic replace:

1. read current registry,
2. update in memory,
3. write a temp YAML file in the same directory,
4. replace the target file.

No cross-process distributed lock is added in phase 1. Atomic replacement avoids partial files; concurrent last-writer-wins behavior is acceptable for the first file-backed version.

## Tests

Add focused tests for:

- registry seed creation, load, save, create, update, and soft delete,
- API auth-compatible CRUD behavior,
- document extraction prompt guidance uses the registry by default,
- explicit `addon_params["entity_types_guidance"]` still overrides registry guidance,
- audit KG sync rejects a mapping that references an inactive or unknown registry type.

Run relevant subsets with:

```bash
./scripts/test.sh tests/extraction/test_entity_extraction_stability.py
./scripts/test.sh tests/api/routes/test_entity_type_routes.py
./scripts/test.sh tests/kg_mapping/test_entity_type_registry.py
./scripts/test.sh tests/api/routes/test_audit_kg_sync_routes.py
```
