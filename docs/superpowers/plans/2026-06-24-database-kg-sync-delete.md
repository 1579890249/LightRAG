# Database KG Sync Delete Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add database-row deletion to `/audit/kg-sync` so stale source rows are removed from LightRAG graph storage safely.

**Architecture:** Treat each database source row as a pseudo-document keyed by its stable `source_id`. Extend `ainsert_custom_kg` to persist provenance indexes, then add a deletion helper that resolves previous sync records into chunk ids and reuses `_purge_doc_chunks_and_kg`. The audit route applies deletes before current upserts and writes sync state only after all apply work succeeds.

**Tech Stack:** Python async LightRAG core, FastAPI route tests, pytest offline tests.

---

### Task 1: Route-Level Delete Apply Contract

**Files:**
- Modify: `tests/api/routes/test_audit_kg_sync_routes.py`
- Modify: `lightrag/api/routers/audit_routes.py`

- [ ] **Step 1: Write the failing route tests**

Add tests that create a previous state with one row absent from the current database. Use a rag stub with `adelete_custom_kg_sources=AsyncMock()` and `ainsert_custom_kg=AsyncMock()`.

```python
def test_audit_kg_sync_apply_deletes_removed_rows_before_upsert(tmp_path):
    mapping_path = tmp_path / "mapping.yaml"
    db_path = tmp_path / "audit.db"
    state_path = tmp_path / "state.json"
    _write_mapping(mapping_path)
    _write_db(db_path)
    state_path.write_text(
        json.dumps(
            [
                {
                    "source": "enterprise",
                    "primary_key": "E000",
                    "source_id": "db://audit/enterprise/E000",
                    "row_hash": "old-hash",
                    "entities": ["Organization:E000"],
                    "relationships": [],
                    "chunks": ["db://audit/enterprise/E000"],
                }
            ]
        ),
        encoding="utf-8",
    )

    calls = []

    async def _delete(records):
        calls.append("delete")
        assert records[0]["primary_key"] == "E000"
        return {"deleted_sources": 1, "deleted_chunks": 1}

    async def _insert(custom_kg):
        calls.append("insert")
        assert custom_kg["entities"][0]["entity_name"] == "Organization:E001"

    rag = SimpleNamespace(
        workspace="audit_customer_ys",
        adelete_custom_kg_sources=AsyncMock(side_effect=_delete),
        ainsert_custom_kg=AsyncMock(side_effect=_insert),
    )
    client = _build_client(rag)

    response = client.post(
        "/audit/kg-sync",
        headers=_HEADERS,
        json={
            "mapping": str(mapping_path),
            "connection_url": f"sqlite:///{db_path}",
            "state": str(state_path),
            "apply": True,
            "write_state": True,
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert calls == ["delete", "insert"]
    assert payload["delete_result"] == {"deleted_sources": 1, "deleted_chunks": 1}
    assert payload["sync_diff"]["delete"] == 1
    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert [record["primary_key"] for record in state] == ["E001"]
```

Add a failure test:

```python
def test_audit_kg_sync_delete_failure_does_not_write_state_or_upsert(tmp_path):
    mapping_path = tmp_path / "mapping.yaml"
    db_path = tmp_path / "audit.db"
    state_path = tmp_path / "state.json"
    _write_mapping(mapping_path)
    _write_db(db_path)
    original_state = [
        {
            "source": "enterprise",
            "primary_key": "E000",
            "source_id": "db://audit/enterprise/E000",
            "row_hash": "old-hash",
            "entities": ["Organization:E000"],
            "relationships": [],
            "chunks": ["db://audit/enterprise/E000"],
        }
    ]
    state_path.write_text(json.dumps(original_state), encoding="utf-8")

    async def _delete(_records):
        raise RuntimeError("delete failed sentinel")

    rag = SimpleNamespace(
        workspace="audit_customer_ys",
        adelete_custom_kg_sources=AsyncMock(side_effect=_delete),
        ainsert_custom_kg=AsyncMock(),
    )
    client = _build_client(rag)

    response = client.post(
        "/audit/kg-sync",
        headers=_HEADERS,
        json={
            "mapping": str(mapping_path),
            "connection_url": f"sqlite:///{db_path}",
            "state": str(state_path),
            "apply": True,
            "write_state": True,
        },
    )

    assert response.status_code == 500
    assert "delete failed sentinel" in response.json()["detail"]
    rag.ainsert_custom_kg.assert_not_called()
    assert json.loads(state_path.read_text(encoding="utf-8")) == original_state
```

- [ ] **Step 2: Run the tests and verify they fail**

Run:

```bash
./scripts/test.sh tests/api/routes/test_audit_kg_sync_routes.py -k "delete"
```

Expected: failures because `adelete_custom_kg_sources` is not called and `delete_result` is absent.

- [ ] **Step 3: Implement minimal route behavior**

In `audit_routes.py`, inside `if request.apply:`, before `await rag.ainsert_custom_kg(...)`, add:

```python
delete_result = None
if sync_diff.to_delete:
    delete_result = await rag.adelete_custom_kg_sources(sync_diff.to_delete)
    summary["delete_result"] = delete_result
```

Then keep the insert call. Do not catch deletion separately; the existing outer exception handler should return HTTP 500 and skip state writes.

- [ ] **Step 4: Run the tests and verify they pass**

Run:

```bash
./scripts/test.sh tests/api/routes/test_audit_kg_sync_routes.py -k "delete"
```

Expected: PASS.

### Task 2: Custom KG Provenance Indexes

**Files:**
- Modify: `tests/pipeline/test_graph_keyed_locks.py` or create focused test file `tests/pipeline/test_custom_kg_provenance.py`
- Modify: `lightrag/lightrag.py`

- [ ] **Step 1: Write failing provenance test**

Create `tests/pipeline/test_custom_kg_provenance.py` with lightweight storage mocks. The test calls `LightRAG.__new__`, seeds async mock storages, calls `ainsert_custom_kg` with one chunk/entity/relation, and asserts:

```python
assert rag.full_entities.upsert.await_args.args[0] == {
    "db://audit/enterprise/E001": {"entity_names": ["Organization:E001"]}
}
assert rag.full_relations.upsert.await_args.args[0] == {
    "db://audit/enterprise/E001": {"relation_pairs": [["Organization:E001", "Project:P1"]]}
}
assert rag.entity_chunks.upsert.await_args.args[0]["Organization:E001"]["chunk_ids"] == [expected_chunk_id]
assert rag.relation_chunks.upsert.await_args.args[0][expected_relation_key]["chunk_ids"] == [expected_chunk_id]
```

Compute `expected_chunk_id` with `compute_mdhash_id(content, prefix="chunk-")` and `expected_relation_key` with `make_relation_chunk_key("Organization:E001", "Project:P1")`.

- [ ] **Step 2: Run the test and verify it fails**

Run:

```bash
./scripts/test.sh tests/pipeline/test_custom_kg_provenance.py
```

Expected: FAIL because `ainsert_custom_kg` does not write these provenance stores.

- [ ] **Step 3: Implement provenance writes in `ainsert_custom_kg`**

During chunk processing, keep `source_id -> chunk_id` in `chunk_to_source_map` as already present. Add accumulators:

```python
full_entities_by_doc: dict[str, set[str]] = {}
full_relations_by_doc: dict[str, set[tuple[str, str]]] = {}
entity_chunks_payload: dict[str, set[str]] = {}
relation_chunks_payload: dict[str, set[str]] = {}
```

When building each entity, if the entity source resolves to a chunk id:

```python
source_doc_id = source_chunk_id
full_entities_by_doc.setdefault(source_doc_id, set()).add(entity_name)
entity_chunks_payload.setdefault(entity_name, set()).add(source_id)
```

When building each relationship, if the relation source resolves to a chunk id:

```python
source_doc_id = source_chunk_id
full_relations_by_doc.setdefault(source_doc_id, set()).add(tuple(sorted((src_id, tgt_id))))
relation_chunks_payload.setdefault(make_relation_chunk_key(src_id, tgt_id), set()).add(source_id)
```

After graph/vdb writes, upsert KV indexes:

```python
if full_entities_by_doc:
    await self.full_entities.upsert({
        doc_id: {"entity_names": sorted(names)}
        for doc_id, names in full_entities_by_doc.items()
    })
if full_relations_by_doc:
    await self.full_relations.upsert({
        doc_id: {"relation_pairs": [list(pair) for pair in sorted(pairs)]}
        for doc_id, pairs in full_relations_by_doc.items()
    })
if entity_chunks_payload and self.entity_chunks:
    await self.entity_chunks.upsert({
        entity_name: {"chunk_ids": sorted(chunk_ids), "count": len(chunk_ids), "updated_at": current_time}
        for entity_name, chunk_ids in entity_chunks_payload.items()
    })
if relation_chunks_payload and self.relation_chunks:
    await self.relation_chunks.upsert({
        relation_key: {"chunk_ids": sorted(chunk_ids), "count": len(chunk_ids), "updated_at": current_time}
        for relation_key, chunk_ids in relation_chunks_payload.items()
    })
```

For existing chunk-tracking rows, merge with old stored chunk ids before upsert so repeated inserts do not erase document provenance.

- [ ] **Step 4: Run the provenance test**

Run:

```bash
./scripts/test.sh tests/pipeline/test_custom_kg_provenance.py
```

Expected: PASS.

### Task 3: LightRAG Custom KG Deletion Helper

**Files:**
- Modify: `tests/pipeline/test_custom_kg_provenance.py`
- Modify: `lightrag/lightrag.py`

- [ ] **Step 1: Write failing deletion helper test**

Add a test using `LightRAG.__new__` with:

- `text_chunks.get_by_ids` returning chunk records with `source_id` equal to the old sync record chunk source id;
- `_purge_doc_chunks_and_kg = AsyncMock()`;
- pipeline status helpers can use the real shared storage helpers.

Call:

```python
result = await rag.adelete_custom_kg_sources([old_record])
```

Assert `_purge_doc_chunks_and_kg` was called with `doc_id="db://audit/enterprise/E001"` and the actual chunk id, and result counts include one source and one chunk.

- [ ] **Step 2: Run the test and verify it fails**

Run:

```bash
./scripts/test.sh tests/pipeline/test_custom_kg_provenance.py -k "delete_custom_kg"
```

Expected: FAIL because method does not exist.

- [ ] **Step 3: Implement `adelete_custom_kg_sources`**

Add method to `LightRAG`:

```python
async def adelete_custom_kg_sources(self, sync_records: list[dict[str, Any]]) -> dict[str, int]:
    pipeline_status = await get_namespace_data("pipeline_status", workspace=self.workspace)
    pipeline_status_lock = get_namespace_lock("pipeline_status", workspace=self.workspace)
    deleted_sources = 0
    deleted_chunks = 0
    for record in sync_records:
        source_id = str(record.get("source_id") or "")
        if not source_id:
            continue
        chunk_ids = await self._resolve_custom_kg_chunk_ids(record)
        if chunk_ids:
            await self._purge_doc_chunks_and_kg(
                source_id,
                chunk_ids,
                pipeline_status=pipeline_status,
                pipeline_status_lock=pipeline_status_lock,
            )
            deleted_chunks += len(chunk_ids)
        else:
            await self.full_entities.delete([source_id])
            await self.full_relations.delete([source_id])
        deleted_sources += 1
    if deleted_sources:
        await self._insert_done()
    return {"deleted_sources": deleted_sources, "deleted_chunks": deleted_chunks}
```

Implement `_resolve_custom_kg_chunk_ids` by scanning `record["chunks"]` source ids against `text_chunks.get_by_ids` if sync record already stores chunk ids, and fallback to deterministic recomputation only when record contains enough content. Prefer persisted index from Task 2.

- [ ] **Step 4: Run deletion helper tests**

Run:

```bash
./scripts/test.sh tests/pipeline/test_custom_kg_provenance.py -k "delete_custom_kg"
```

Expected: PASS.

### Task 4: Documentation and Focused Verification

**Files:**
- Modify: `docs/deployment/lightrag-server-172-16-1-203.md`
- Modify: `docs/superpowers/specs/2026-06-24-database-kg-sync-delete-design.md` only if implementation details differ.

- [ ] **Step 1: Update deployment notes**

Replace the “Physical deletion cleanup is not implemented” item with a note that `/audit/kg-sync apply=true` applies `sync_diff.delete` using sync state, and that durable state storage is required for reliable incremental deletes.

- [ ] **Step 2: Run route and provenance tests**

Run:

```bash
./scripts/test.sh tests/api/routes/test_audit_kg_sync_routes.py tests/pipeline/test_custom_kg_provenance.py tests/kg_mapping
```

Expected: PASS.

- [ ] **Step 3: Run lint on touched Python files**

Run:

```bash
ruff check lightrag/lightrag.py lightrag/api/routers/audit_routes.py tests/api/routes/test_audit_kg_sync_routes.py tests/pipeline/test_custom_kg_provenance.py
```

Expected: PASS.
