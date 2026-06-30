# LightRAG Server Deployment Notes

## Server

- Host: `172.16.1.203`
- SSH user: `spfx`
- Project path: `/mnt/github/LightRAG`
- Python virtualenv path created by user: `/mnt/github/LightRAG/.venv`
- Compose file: `/mnt/github/LightRAG/docker-compose-full.yml`
- Env file: `/mnt/github/LightRAG/.env`

## SSH Access

Local SSH key files on the Windows workstation:

- Private key: `C:\Users\15798\.codex\memories\ssh-keys\spfx_172_16_1_203`
- Public key: `C:\Users\15798\.codex\memories\ssh-keys\spfx_172_16_1_203.pub`

The public key has been installed for `spfx@172.16.1.203`.

Connect command:

```powershell
ssh -i C:\Users\15798\.codex\memories\ssh-keys\spfx_172_16_1_203 spfx@172.16.1.203
```

Non-interactive command example:

```powershell
ssh -i C:\Users\15798\.codex\memories\ssh-keys\spfx_172_16_1_203 -o StrictHostKeyChecking=accept-new spfx@172.16.1.203 "cd /mnt/github/LightRAG && docker compose -f docker-compose-full.yml ps"
```

## Current Access

- WebUI: `http://172.16.1.203:9621/lightRag/webui/`
- API health: `http://172.16.1.203:9621/health`
- PostgreSQL from LAN:
  - Host: `172.16.1.203`
  - Port: `5432`
  - Database: `rag`
  - User: `rag`
  - Password: `rag`

## Docker Services

Current compose services:

- `lightrag`: `ghcr.io/hkuds/lightrag-custom:latest`, mapped as `0.0.0.0:9621->9621/tcp`
- `postgres`: `pgvector/pgvector:pg18`, mapped as `0.0.0.0:5432->5432/tcp`
- `neo4j`: `neo4j:5-community`
- `milvus`: `milvusdb/milvus:v2.6.11-gpu`
- `milvus-etcd`: `quay.io/coreos/etcd:v3.5.25`
- `milvus-minio`: `minio/minio:RELEASE.2025-09-07T16-13-09Z`

Removed from `docker-compose-full.yml` because model services already exist externally:

- `vllm-embed`
- `vllm-rerank`

## LightRAG Runtime Status

Latest verified status:

- Health: `healthy`
- WebUI available: `true`
- WebUI brand name: `知识库系统`
- WebUI title: `审计数据库`

Storage backends:

- KV storage: `PGKVStorage`
- Doc status storage: `PGDocStatusStorage`
- Graph storage: `Neo4JStorage`
- Vector storage: `MilvusVectorDBStorage`

Model endpoints:

- LLM binding host: `http://172.16.102.154:8008/v1`
- Embedding binding host: `http://172.16.102.154:8008/v1`
- Rerank binding: `cohere`
- Rerank model: `bge-reranker-v2-m3`
- Rerank endpoint: `http://172.16.102.154:8008/v1/rerank`
- Rerank enabled: `true`

Parser:

- `LIGHTRAG_PARSER=*:native-iteP;*:mineru-iteP;*:legacy-R`
- MinerU mode: `local`
- MinerU endpoint from container: `http://host.docker.internal:8003`
- MinerU options:
  - `language=ch`
  - `enable_table=true`
  - `enable_formula=true`
  - `local_backend=hybrid-auto-engine`
  - `local_parse_method=auto`
  - `local_image_analysis=false`

MinerU service is running on the same server port `8003`; container connectivity to `http://host.docker.internal:8003/docs` has been verified.

## Important Commands

Start or restart LightRAG stack:

```bash
cd /mnt/github/LightRAG
docker compose -f docker-compose-full.yml up -d
```

Restart only LightRAG after `.env` changes:

```bash
cd /mnt/github/LightRAG
docker compose -f docker-compose-full.yml up -d --no-build --force-recreate lightrag
```

Update audit KG mapping config:

```bash
cd /mnt/github/LightRAG
vi configs/kg_mappings/audit_customer_ys.yaml
curl -sS -X POST http://127.0.0.1:9621/lightRag/audit/kg-sync \
  -H 'Content-Type: application/json' \
  -d '{"apply":true,"write_state":true,"workspace":"audit_customer_ys"}'
```

`configs/kg_mappings` is bind-mounted into the container at
`/app/configs/kg_mappings` as read-only, so mapping-only YAML changes do not
require a Docker image rebuild. Recreate `lightrag` only when changing the
compose mount itself; rebuild the image only when Python/WebUI/image contents
change.

Generated KG mapping drafts and generation records should be written under the
container data directory so they can be produced at runtime without restarting
LightRAG:

```yaml
- ./data/kg_mappings:/app/data/kg_mappings
- ./data/kg_mapping_generations:/app/data/kg_mapping_generations
```

The auto-generation API defaults to these container paths:

```text
/app/data/kg_mappings
/app/data/kg_mapping_generations
```

Generate a KG mapping from the business database schema and optional
relationship metadata files:

```bash
cd /mnt/github/LightRAG
curl -sS -X POST http://127.0.0.1:9621/lightRag/audit/kg-mapping/generate \
  -F connection_url=postgresql://rag:rag@postgres:5432/audit \
  -F schema=public \
  -F database_name=audit_auto \
  -F workspace=audit_customer_ys \
  -F mode=merge \
  -F sample_limit=100 \
  -F metadata_files=@docs/examples/audit-kg-relationship-metadata.sql
```

The deployed route paths are under the configured API prefix:

```text
POST /lightRag/audit/kg-mapping/generate
GET  /lightRag/audit/kg-mapping/generation/{generation_id}
POST /lightRag/audit/kg-mapping/generation/{generation_id}/preview
POST /lightRag/audit/kg-mapping/generation/{generation_id}/publish
POST /lightRag/audit/kg-mapping/generation/{generation_id}/rollback
```

The same route can be called without `metadata_files`; in that case it only
uses live PostgreSQL schema introspection, foreign keys, table/column names, and
sampled data coverage. Supported metadata upload formats are `.json`, `.yaml`,
`.yml`, `.csv`, `.sql`, `.ddl`, and `.xlsx`. Uploaded SQL/DDL/Excel/CSV files
are treated as relationship metadata, not as SQL to execute.

Default audit generation excludes these tables from the generated graph mapping:

```text
audit_rule
audit_rule_graph_config
audit_result
project_alias
audit_rule*_backup_*
```

Preview a generated mapping before writing anything into LightRAG:

```bash
curl -sS -X POST \
  http://127.0.0.1:9621/lightRag/audit/kg-mapping/generation/{generation_id}/preview
```

Publish a generated mapping and immediately sync its structured graph into the
current LightRAG workspace:

```bash
curl -sS -X POST \
  http://127.0.0.1:9621/lightRag/audit/kg-mapping/generation/{generation_id}/publish \
  -H 'Content-Type: application/json' \
  -d '{"apply":true,"write_state":true}'
```

Publishing copies the generated YAML to `{database_name}.current.yaml`, runs the
same audit KG sync path as `POST /audit/kg-sync`, and records the sync result in
the generation record. Use `apply=false` to publish/dry-run without inserting
into LightRAG. Rollback republishes the previous published generation and can
also re-sync the graph:

```bash
curl -sS -X POST \
  http://127.0.0.1:9621/lightRag/audit/kg-mapping/generation/{generation_id}/rollback \
  -H 'Content-Type: application/json' \
  -d '{"apply":true,"write_state":true}'
```

Reference docs for this feature:

- `docs/kg-mapping-auto-generation-api.md`
- `docs/kg-mapping-auto-generation-design.md`
- `docs/examples/audit-kg-relationship-metadata.sql`

Restart Postgres and LightRAG after Postgres port mapping changes:

```bash
cd /mnt/github/LightRAG
docker compose -f docker-compose-full.yml up -d --no-build --force-recreate postgres lightrag
```

Build custom LightRAG image:

```bash
cd /mnt/github/LightRAG
docker compose -f docker-compose-full.yml build lightrag
```

If Docker Hub metadata fetch times out for base images, use the overlay build path
based on the currently deployed custom image:

```bash
cd /mnt/github/LightRAG
docker image tag ghcr.io/hkuds/lightrag-custom:latest ghcr.io/hkuds/lightrag-custom:audit-base-20260623
docker build -f Dockerfile.audit-overlay -t ghcr.io/hkuds/lightrag-custom:latest .
docker compose -f docker-compose-full.yml up -d --no-deps lightrag
```

WebUI build safety note:

- As of the 2026-06-29 investigation, no rebuild was performed during the
  check. The running container had mixed WebUI assets: a newer
  `assets/index-*.js` containing `hideHeader` / `initialTab`, but
  `/app/lightrag/api/webui/index.html` still pointed at an older JS bundle.
- Later on 2026-06-29, the image was rebuilt with `Dockerfile.audit-overlay`
  and the `lightrag` container was recreated. The previous image was backed up
  as `ghcr.io/hkuds/lightrag-custom:before-webui-fix-20260629`.
  Post-rebuild verification showed `/app/lightrag/api/webui/index.html`
  pointing at `assets/index-3yheokDn.js`, and that bundle contains both
  `hideHeader` and `initialTab`.
- Do not use a backend-only overlay that blindly runs
  `COPY lightrag/ ./lightrag/` unless it also preserves or rebuilds
  `/app/lightrag/api/webui`. The repository tree can contain stale built
  WebUI files under `lightrag/api/webui`, and copying that directory into the
  image can make frontend routes look "lost".
- When WebUI source changed, or when in doubt, rebuild with
  `Dockerfile.audit-overlay`. It runs `bun run build` from
  `lightrag_webui/` and then copies the fresh `/app/lightrag/api/webui`
  bundle into the image.
- Do not use `Dockerfile.audit-code-overlay` as-is for production deploys if
  the current WebUI must be preserved. That file copies `lightrag/` directly
  and can overwrite the runtime WebUI bundle with stale checked-out artifacts.
- If a backend-only overlay is needed, it must either exclude
  `lightrag/api/webui` from the copied source or restore
  `/app/lightrag/api/webui` from the base image after copying backend code.

Verify the active WebUI bundle after any image rebuild:

```bash
docker exec lightrag-lightrag-1 sh -lc '
WEBUI=/app/lightrag/api/webui
MAIN="$(grep -o "assets/index-[^\" ]*\\.js" "$WEBUI/index.html" | head -1)"
echo "index.html main bundle: $MAIN"
test -n "$MAIN"
grep -q "hideHeader" "$WEBUI/$MAIN" && echo "hideHeader: yes" || echo "hideHeader: no"
grep -q "initialTab" "$WEBUI/$MAIN" && echo "initialTab: yes" || echo "initialTab: no"
'
```

Expected for the standalone routes:

```text
hideHeader: yes
initialTab: yes
```

Standalone WebUI route examples:

```text
http://172.16.1.203:9621/lightRag/webui/#/knowledge-graph
http://172.16.1.203:9621/lightRag/webui/#/retrieval
```

Check service status:

```bash
cd /mnt/github/LightRAG
docker compose -f docker-compose-full.yml ps
curl -fsS http://127.0.0.1:9621/health
```

Connect to Postgres from the server:

```bash
psql -h 127.0.0.1 -p 5432 -U rag -d rag
```

Connect to Postgres from LAN:

```bash
psql -h 172.16.1.203 -p 5432 -U rag -d rag
```

## Code Changes Completed

Custom WebUI branding:

- Added backend helper: `lightrag/api/webui_branding.py`
- Added `WEBUI_BRAND_NAME` support in API responses:
  - `/auth-status`
  - `/login`
  - `/health`
- Updated WebUI state and header to display backend-provided brand name.
- Added regression test: `tests/api/config/test_webui_branding.py`

Tender KG adapter:

- Added package: `lightrag/tender_kg/`
- Added tests: `tests/tender_kg/`
- Purpose: build LightRAG `custom_kg` payloads from tender/bidding structured database records plus document references.

Audit database to KG sync:

- Added configurable database mapping package: `lightrag/kg_mapping/`
- Added audit mapping file: `configs/kg_mappings/audit_customer_ys.yaml`
- Added CLI dry-run/apply helper: `scripts/audit_kg_sync.py`
- Added internal API router: `lightrag/api/routers/audit_routes.py`
- Registered the router in `lightrag/api/lightrag_server.py`
- Added route tests: `tests/api/routes/test_audit_kg_sync_routes.py`
- Added mapping tests: `tests/kg_mapping/`
- Route: `POST /audit/kg-sync`
- Default mapping: `configs/kg_mappings/audit_customer_ys.yaml`
- Default business DB URL inside compose network: `postgresql://rag:rag@postgres:5432/audit`
- Default target workspace: current LightRAG workspace, currently `audit_customer_ys`
- The route uses the existing API authentication dependency.
- The synchronous DB load and KG build work run in a worker thread so PostgreSQL fallback through `asyncpg` does not call `asyncio.run()` inside the FastAPI event loop.

Automatic KG mapping generation from database schema and SQL/DDL metadata:

- Added automatic mapping generator: `lightrag/kg_mapping/auto_generator.py`
- Added generation API module: `lightrag/kg_mapping/auto_generation_api.py`
- Registered generation routes through `lightrag/api/routers/audit_routes.py`
- Added API documentation:
  - `docs/kg-mapping-auto-generation-api.md`
  - `docs/kg-mapping-auto-generation-design.md`
- Added example relationship metadata file:
  - `docs/examples/audit-kg-relationship-metadata.sql`
- Added generation tests:
  - `tests/kg_mapping/test_auto_generator.py`
  - `tests/kg_mapping/test_auto_generation_api.py`
- Routes:
  - `POST /audit/kg-mapping/generate`
  - `GET /audit/kg-mapping/generation/{generation_id}`
  - `POST /audit/kg-mapping/generation/{generation_id}/preview`
  - `POST /audit/kg-mapping/generation/{generation_id}/publish`
  - `POST /audit/kg-mapping/generation/{generation_id}/rollback`
- Default runtime mapping output directory: `/app/data/kg_mappings`
- Default runtime generation record directory: `/app/data/kg_mapping_generations`
- Supported metadata upload formats: `.json`, `.yaml`, `.yml`, `.csv`, `.sql`, `.ddl`, `.xlsx`
- `generate` only creates a draft mapping and generation record. Real graph writes
  happen when `publish` calls audit KG sync with `apply=true`.
- Publish and rollback both reuse the audit KG sync path, so they respect the
  same workspace, state-file, provenance-delete, and `ainsert_custom_kg` behavior.
- Optional LLM enhancement can improve readable labels, entity name templates,
  description templates, and relation type names without changing SQL queries,
  source names, primary keys, ID fields, or relationship endpoints.
- The deployed server uses the `/lightRag` API prefix, so operational calls use
  `/lightRag/audit/kg-mapping/...` even though the router definitions are
  documented as `/audit/kg-mapping/...`.

Current audit mapping sources:

- `enterprise`
- `person`
- `project`
- `bid_record`
- `person_relation`
- `enterprise_certificate`
- `enterprise_revenue`
- `person_enterprise_position`
- `project_person_role`
- `bid_person_role`
- `enterprise_shareholding`

The `project_evaluator` table is intentionally not mapped as a source fact table.
Risk points in that table are expected to be generated later by LLM analysis,
not inserted as authoritative graph facts.

Current audit mapping fields include these schema changes:

- `enterprise.business_scope` is included in Organization descriptions.
- `enterprise.legal_person_name` has been removed. Legal representative and
  personnel-role facts should be modeled through `person_enterprise_position`
  instead of duplicate name text on `enterprise`.
- `person.phone` is included in Person descriptions. The live table uses `phone`, not `iphone`.
- `project.bid_time` is included in Project descriptions.
- `project.tender_org_id` is expected to reference `enterprise.enterprise_id`
  when deterministic tender-side graph reasoning is needed. The old
  `project.tender_org` text field can stay as display/fallback text.
- `person_relation.confidence` is no longer used.
- `project_person_role.id` and `bid_person_role.id` are integer auto-increment
  primary keys in the DDL metadata example.
- `project_person_role` maps project-side personnel roles, such as tender
  contacts, procurement handlers, agency staff, and evaluators.
- `bid_person_role` maps bid-side personnel roles, such as bid contacts,
  authorized agents, project managers, and legal representatives.
- `enterprise_shareholding` maps name-based company shareholding records.
  The live table stores `enterprise_name`, `holder_type`, `holder_name`, and
  `shareholding_ratio`; KG sync resolves names to `enterprise` / `person`
  nodes at query time.
- `enterprise_shareholding.holder_type=1` means the holder is a natural person
  matched by `person.name`; `holder_type=2` means the holder is an enterprise
  matched by `enterprise.enterprise_name`.
- Shareholding facts are modeled as `ShareholdingRecord` nodes linked to the
  target company and shareholder. This preserves record-level provenance and
  supports later ownership-penetration reasoning.
- Equity penetration is not limited to company-company ownership. A natural
  person shareholder is also a `Person` node, so paths can cover:
  shareholder -> shareholding record -> bidder company -> bid submission ->
  project, and shareholder -> shareholding record -> tender organization.
  This supports cases where one shareholder holds multiple companies, including
  companies on the bidder side and the tender side.

Audit DB migration/sample-data scripts:

- `scripts/audit_role_tables_migration.sql`: tender/bid personnel role tables
  and sample role data.
- `scripts/audit_shareholding_migration.sql`: `enterprise_shareholding` table
  and sample shareholding data for company/person ownership-penetration checks.
- `scripts/audit_graph_rule_config_migration.sql`: hidden
  `audit_rule_graph_config` table plus a default compiled path config for
  `audit_rule.rule_type = '招投标人际关系风险'`.

## Deterministic Multi-Hop Graph Path Query

The first deterministic graph reasoning API is:

```text
POST /lightRag/audit/graph/paths/query
```

It accepts two endpoint names and a business scenario:

```json
{
  "start": {"name": "张三"},
  "end": {"name": "深圳华信科技有限公司"},
  "business_type": "招投标人际关系风险",
  "max_depth": 4,
  "limit": 50
}
```

`business_type` is driven by `audit_rule.rule_type`. Business users still only
maintain rule names, rule types, and natural-language rule basis text in
`audit_rule`; they do not need to know table names, graph relation names, or
JSON configuration syntax.

Machine-executable path constraints are stored in the hidden table
`audit_rule_graph_config`:

```sql
CREATE TABLE audit_rule_graph_config (
  id integer GENERATED BY DEFAULT AS IDENTITY PRIMARY KEY,
  rule_id integer NOT NULL REFERENCES audit_rule(id) ON DELETE CASCADE,
  config jsonb NOT NULL,
  basis_hash text,
  status text NOT NULL DEFAULT 'active',
  created_at timestamp NOT NULL DEFAULT now(),
  updated_at timestamp NOT NULL DEFAULT now()
);
```

The intended workflow is:

1. Business users maintain `audit_rule.rule_type` and `audit_rule.rule_basis`.
2. A system process or LLM compiler reads the rule basis plus the current KG
   mapping and generates a graph config.
3. The backend validates that generated entity types and relation types exist
   in the current KG mapping.
4. The validated config is saved in `audit_rule_graph_config`.
5. The path query API reads active configs by `business_type`, performs a
   deterministic bounded graph traversal, and returns structured paths.

Example config:

```json
{
  "allowed_entity_types": [
    "Person",
    "Organization",
    "Project",
    "BidSubmission",
    "ShareholdingRecord"
  ],
  "relation_types": [
    "PERSON_RELATED",
    "HOLDS_POSITION",
    "PROJECT_PERSON_ROLE",
    "PROJECT_ROLE_ORG",
    "BID_PERSON_ROLE",
    "BID_ROLE_ORG",
    "BIDDER",
    "FOR_PROJECT",
    "TENDERED_BY",
    "SHAREHOLDING_TARGET",
    "NATURAL_PERSON_SHAREHOLDER",
    "ENTERPRISE_SHAREHOLDER"
  ],
  "default_max_depth": 4
}
```

The path API is deterministic: LLMs may compile rule basis into the hidden
config or explain returned paths, but they do not decide whether a path exists.
If no active graph config exists for the requested `business_type`, the API
returns a clear error instead of falling back to ad hoc LLM reasoning.

Hidden graph configs can be created or replaced through:

```text
POST /lightRag/audit/graph/rule-configs/upsert
```

For business-facing use, the request does not need entity types or relation
types. The backend picks a built-in template by `rule_type`, filters it against
the current KG mapping, validates it, and stores the result in
`audit_rule_graph_config`:

```json
{
  "rule_type": "人际关系",
  "rule_name": "人员关联关系校验",
  "default_max_depth": 4
}
```

The response includes the compiled hidden config so operators can inspect what
was actually saved:

```json
{
  "rule_type": "人际关系",
  "source": "builtin_template",
  "configs": [
    {
      "rule_id": 5,
      "rule_name": "人员关联关系校验",
      "config_id": 12,
      "status": "active",
      "config": {
        "allowed_entity_types": ["Person", "Organization"],
        "relation_types": ["PERSON_RELATED", "HOLDS_POSITION"],
        "default_max_depth": 4
      }
    }
  ]
}
```

Technical users may still pass an explicit `config` for debugging or special
cases. Explicit configs are validated against the current mapping and rejected
if they contain unknown entity or relation types.

### Neo4j Cypher Optimization Design

The path query keeps the backend-portable Python BFS implementation as a
fallback, but Neo4j deployments should use a dedicated Cypher fast path. The
current graph schema stores all LightRAG edges as Neo4j relationship type
`DIRECTED`; the business relation type is stored in the relationship property
`keywords`.

The optimized flow is:

1. Load and merge active hidden configs for the requested `business_type`.
2. Validate the start and end nodes through the normal graph storage API.
3. If graph storage exposes the Neo4j driver and workspace label helpers, run a
   single bounded path Cypher query.
4. If Neo4j fast path is unavailable or fails, keep the existing Python BFS as
   the portable fallback.

Cypher shape:

```cypher
MATCH (start:`audit_customer_ys` {entity_id: $start_name})
MATCH (end:`audit_customer_ys` {entity_id: $end_name})
MATCH p = (start)-[:DIRECTED*1..4]-(end)
WHERE all(n IN nodes(p) WHERE n.entity_type IN $allowed_entity_types)
  AND all(r IN relationships(p) WHERE r.keywords IN $relation_types)
  AND all(n IN nodes(p) WHERE single(m IN nodes(p) WHERE elementId(m) = elementId(n)))
RETURN
  [n IN nodes(p) | properties(n)] AS nodes,
  [n IN nodes(p) | n.entity_id] AS node_names,
  [r IN relationships(p) | properties(r)] AS edges,
  [r IN relationships(p) | startNode(r).entity_id] AS edge_sources,
  [r IN relationships(p) | endNode(r).entity_id] AS edge_targets,
  length(p) AS depth
ORDER BY depth ASC
LIMIT $limit
```

`max_depth` remains bounded by the API limit (`1..6`) and is the only value
interpolated into the query shape; names, entity types, relation types, and
limits are passed as Cypher parameters. This turns many small
`get_node_edges` / `get_edge` / `get_node` round trips into one Neo4j query.

Recommended indexes:

```cypher
CREATE INDEX entity_id_idx IF NOT EXISTS
FOR (n:`audit_customer_ys`)
ON (n.entity_id);

CREATE INDEX entity_type_idx IF NOT EXISTS
FOR (n:`audit_customer_ys`)
ON (n.entity_type);

CREATE INDEX rel_keywords_idx IF NOT EXISTS
FOR ()-[r:DIRECTED]-()
ON (r.keywords);
```

Minimal request:

```bash
curl -sS -X POST http://127.0.0.1:9621/lightRag/audit/graph/paths/query \
  -H 'Content-Type: application/json' \
  -d '{
    "start": {"name": "张三"},
    "end": {"name": "深圳华信科技有限公司"},
    "business_type": "招投标人际关系风险",
    "max_depth": 4,
    "limit": 20
  }'
```

Equity examples:

Natural person shareholder -> bidder company -> current project:

```json
{
  "start": {"name": "张伟"},
  "end": {"name": "广新智慧采购监管平台"},
  "business_type": "股权关系",
  "max_depth": 4,
  "limit": 20
}
```

Expected path shape:

```text
张伟
-> 张伟持有深圳华信科技有限公司42.50%股权（1）
-> 深圳华信科技有限公司
-> 深圳华信科技有限公司投标广新智慧采购监管平台（B101）
-> 广新智慧采购监管平台
```

Natural person shareholder -> tender organization:

```json
{
  "start": {"name": "张伟"},
  "end": {"name": "广新控股集团"},
  "business_type": "股权关系",
  "max_depth": 2,
  "limit": 20
}
```

Expected path shape:

```text
张伟
-> 张伟持有广新控股集团12.00%股权（2）
-> 广新控股集团
```

The response returns the matched rules and deterministic paths:

```json
{
  "business_type": "招投标人际关系风险",
  "rules": [
    {
      "rule_id": 1,
      "rule_name": "投标人与招标人关联风险",
      "rule_type": "招投标人际关系风险"
    }
  ],
  "paths": [
    {
      "depth": 2,
      "nodes": [],
      "edges": []
    }
  ]
}
```

Manual sync request examples:

Dry-run only:

```json
{
  "apply": false,
  "write_state": false
}
```

Apply to the current workspace and write sync state:

```json
{
  "apply": true,
  "write_state": true,
  "workspace": "audit_customer_ys"
}
```

Delete graph data generated from database sources without deleting business
database rows:

```bash
curl -sS -X POST http://127.0.0.1:9621/lightRag/audit/kg-source/delete \
  -H 'Content-Type: application/json' \
  -d '{
    "source":"enterprise_shareholding",
    "primary_key":"1",
    "workspace":"audit_customer_ys",
    "remove_from_state":true
  }'
```

Omit `primary_key` to delete every synced graph source row for a table/source:

```json
{
  "source": "enterprise_shareholding",
  "workspace": "audit_customer_ys",
  "remove_from_state": true
}
```

This endpoint only calls `LightRAG.adelete_custom_kg_sources(...)` and optionally
removes matched records from the KG sync state file. It does not execute
`DELETE`, `TRUNCATE`, or `DROP` against the business PostgreSQL database. If the
business rows still exist, a later `/lightRag/audit/kg-sync` with `apply=true`
can insert them into the graph again.

Verified result on 2026-06-23 after applying:

```json
{
  "schema_version": "audit_kg_v1",
  "database_name": "audit",
  "workspace": "audit_customer_ys",
  "sources": {
    "enterprise": 6,
    "person": 12,
    "project": 4,
    "bid_record": 12,
    "person_relation": 2
  },
  "custom_kg": {
    "chunks": 36,
    "entities": 34,
    "relationships": 34
  },
  "applied": true,
  "apply_result": {
    "inserted_chunks": 36,
    "inserted_entities": 34,
    "inserted_relationships": 34
  }
}
```

Notes on sync behavior:

- `apply=true` first applies `sync_diff.delete` through
  `LightRAG.adelete_custom_kg_sources(...)`, then calls
  `LightRAG.ainsert_custom_kg(...)` for current rows.
- The write path is upsert-oriented. The `inserted_*` response fields mean
  "submitted to LightRAG for insert/update", not necessarily brand-new rows in
  every backend.
- `sync_diff` is calculated from the JSON state file. If the state file is
  missing, all source rows are reported as inserts.
- Deleted DB records are removed from the graph using source-row provenance
  recorded during custom KG insertion. Shared entities and relationships are
  rebuilt or retained when other database rows or documents still reference
  them.
- The default state path is
  `/app/data/audit_kg_sync/audit_kg_state_server.json` inside the container,
  mounted from host path `./data/audit_kg_sync/audit_kg_state_server.json`.
- The default mapping directory is `/app/configs/kg_mappings` inside the
  container, mounted read-only from host path `./configs/kg_mappings`.
  Therefore, editing `configs/kg_mappings/audit_customer_ys.yaml` on the host is
  visible to the running container without rebuilding the image. Run
  `POST /lightRag/audit/kg-sync` after editing the mapping to apply it to the
  graph.
- Document ingestion and database KG sync are independent. They can be run in
  either order, and both write into the same `audit_customer_ys` workspace.

Not done yet:

- No automatic trigger exists for database changes. Sync is manually triggered
  through `POST /audit/kg-sync`.
- No CDC, scheduler, or DB trigger integration has been added.
- There is no UI button for this internal route yet.
- LLM-based audit violation analysis has not been wired to this route yet. The
  current work only inserts structured database facts into the LightRAG graph.
- Cross-source entity linking between document-extracted entities and database
  entity IDs still needs a dedicated linking strategy.

## Current Git/Workspace Notes

The server workspace contains local uncommitted changes for:

- Deployment compose/env migration
- Custom WebUI branding
- Tender KG adapter
- Test files

Backups created during migration include:

- `.env.bak.20260623104604`
- `.env.bak.rerank.20260623114829`
- `docker-compose-full.yml.bak.20260623104604`

## Next Work: Tender/Bidding Knowledge Graph Plan

Target scenario:

- Projects
- Tendering companies
- Bidding companies
- Company personnel
- Evaluation experts/personnel
- Bid documents and tender documents
- Tender/bidding/evaluation events

The intended design is hybrid ingestion:

- Structured database records provide authoritative entities and event facts.
- Documents provide textual evidence, clauses, descriptions, and additional extracted entities.
- The integration layer maps both sources into LightRAG graph entities/relations and document chunks.
- Cross-source IDs should be stable and deterministic so the same project/company/person from tables and documents merge into one graph node.

Initial implementation direction:

- Keep the domain adapter outside LightRAG core as `lightrag/tender_kg`.
- Use real database source connectors, not an in-memory-only builder.
- Use LightRAG `ainsert_custom_kg` for structured graph insertion.
- Use existing document ingestion for tender/bid documents, with file paths retained for citation.
- Add a linking layer that maps document references to structured entities/events.

Open items for the next phase:

- Confirm source database type and connection string.
- Confirm table names and schemas for project/company/person/event/document records.
- Define canonical IDs for project, company, person, tender event, bid event, evaluation event, and document.
- Define graph schema: entity types, relation types, relation direction, and key attributes.
- Decide whether initial import is one-time batch, scheduled sync, or incremental CDC-style sync.

## Customer Feedback: Semi-Automatic Entity Types and Identifier Nodes

Feedback from the customer demo:

1. Entity types should support a semi-automatic workflow. The system should
   initialize with a curated set of entity types, then allow the model to
   suggest additional entity types from documents when the existing set is not
   sufficient.
2. Structured and unstructured graph data should be considered together. The
   customer asked whether structured database nodes and document-extracted nodes
   should use the same entity type system.
3. Not every piece of document content should become a graph node. The customer
   specifically asked whether high-value identifiers such as email addresses and
   phone numbers can be queried to find projects, people, or organizations that
   share the same identifier.

Recommended design:

- Use one canonical entity type registry for both structured data and
  unstructured document extraction. Structured KG mappings and document
  extraction prompts should reference the same approved type names.
- Keep structured data deterministic. Database-to-KG sync should continue to
  use explicit YAML mappings such as `configs/kg_mappings/audit_customer_ys.yaml`.
- Let document extraction use LLM suggestions, but do not let the model create
  production entity types directly. Unknown or useful new types should be saved
  as candidate entity types, then deduplicated, normalized, reviewed, and
  approved before entering the canonical registry.
- After approval, the new entity type should be fed back into both paths:
  document extraction prompt guidance and structured KG mapping generation.
- Do not turn full document content into graph nodes. Keep document text as
  chunks for semantic retrieval, citation, and explanation. Only business
  objects, events, relationships, and high-value identifiers should become graph
  entities.

Initial canonical type seed:

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

Identifier modeling direction:

- Model phone numbers, email addresses, unified social credit codes, bank
  accounts, identity numbers, and other high-value identifiers as first-class
  nodes when they support audit queries.
- Link business entities to identifier nodes with explicit relation types, for
  example:
  - `Person` / `Organization` / `Project` -> `CONTACT_PHONE` -> `PhoneNumber`
  - `Person` / `Organization` / `Project` -> `CONTACT_EMAIL` -> `EmailAddress`
  - `Organization` -> `HAS_CREDIT_CODE` -> `Identifier`
- This enables deterministic graph queries such as:
  - Which people, organizations, or projects use the same phone number?
  - Which bidders and tender-side contacts share an email address?
  - Which companies or people are connected through repeated identifier reuse?

Example path shape:

```text
Person/Organization/Project
  -> CONTACT_PHONE
PhoneNumber
  <- CONTACT_PHONE
Person/Organization/Project
```

Product workflow proposal:

1. Maintain an approved entity type registry.
2. During document extraction, map entities to approved types where possible.
3. If no approved type fits, extract the entity as `Other` and emit a candidate
   type suggestion with sample entity names, evidence chunks, and confidence.
4. Aggregate candidate types across documents, merge obvious duplicates, and
   show them in an operator review queue.
5. After approval, update the registry and regenerate the extraction guidance
   used by document ingestion.
6. Re-run extraction or perform incremental enrichment for affected documents
   if the approved type materially changes the graph.

Implementation notes for a future phase:

- Add a registry file or table for approved entity types, with fields such as
  `name`, `label`, `description`, `examples`, `status`, `source`, and
  `created_at`.
- Add a candidate type table or JSON store for LLM-proposed types, including
  evidence and source document/chunk references.
- Update entity extraction prompt construction so approved registry entries
  replace or extend the current built-in `default_entity_types_guidance`.
- Extend KG mapping generation so structured mappings validate against the same
  registry.
- Extend the audit graph rule config validator so hidden rule configs can use
  newly approved entity types and identifier relation types.
- Add deterministic graph path/query APIs for identifier reuse, starting with
  phone and email reuse across people, organizations, projects, and bid records.
