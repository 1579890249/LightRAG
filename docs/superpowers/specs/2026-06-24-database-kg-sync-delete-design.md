# Database KG Sync Delete Design

## Goal

Implement deletion for database-backed KG sync so `/audit/kg-sync` can remove
graph contributions for source rows that no longer exist in the business
database.

## Scope

This design covers the audit database sync path first. It does not replace the
existing document deletion APIs. Uploaded documents continue to use
`adelete_by_doc_id`; database rows use sync-state-driven deletion through
`/audit/kg-sync`.

## Current State

The database mapping layer already builds stable source-row records:

- `ConfigurableKGBuilder` emits one chunk per source row with a stable
  `source_id`, such as `db://audit/enterprise/E001`.
- Each sync record stores the row identity, row hash, emitted entities,
  emitted relationships, and source-row chunks.
- `diff_sync_records` already classifies deleted rows as `to_delete`.
- `/audit/kg-sync` currently inserts or updates current `custom_kg` rows but
  does not apply `to_delete`, so stale graph facts can remain.

Document deletion already has the right safety model: remove only the deleted
document's chunk provenance, delete graph items only when no sources remain, and
rebuild items that still have other sources. Database deletion should reuse that
model instead of deleting entities or relationships directly by name.

## Recommended Architecture

Treat each database source row as a deletable pseudo-document.

`ainsert_custom_kg` should persist enough provenance to make the pseudo-document
deletable later:

- map the source-row `source_id` to the actual computed chunk id;
- write `full_entities[source_id]` with emitted entity names;
- write `full_relations[source_id]` with emitted relation pairs;
- update `entity_chunks` and `relation_chunks` so shared graph items can be
  pruned or rebuilt safely.

Add a LightRAG-level deletion helper for database sync records, for example:

```python
await rag.adelete_custom_kg_sources(sync_records)
```

The helper accepts previous sync records, resolves each record's stored source
row chunks to actual chunk ids, and calls the same purge behavior used by
document reprocessing/deletion. This keeps shared entities and relationships
intact when they still have provenance from other database rows or documents.

## API Behavior

For `POST /audit/kg-sync`:

- dry run keeps current behavior and reports `sync_diff.delete`;
- `apply=true` first deletes `sync_diff.to_delete`;
- after deletion succeeds, it upserts the current `custom_kg`;
- `write_state=true` writes the new state only after all requested apply work
  succeeds.

The response should include deletion counts alongside existing insert/update
counts, for example:

```json
{
  "applied": true,
  "delete_result": {
    "deleted_sources": 1,
    "deleted_chunks": 1,
    "deleted_entities": 0,
    "deleted_relationships": 0,
    "rebuilt_entities": 1,
    "rebuilt_relationships": 0
  }
}
```

Exact field names can stay close to the implementation, but failures must not
advance the state file.

## Error Handling

Deletion is fail-closed:

- if deleting stale rows fails, do not upsert new KG payloads in the same request;
- do not write the new state file after any deletion or upsert failure;
- return an API error with enough detail to retry the same request.

The state file remains the retry anchor. A failed deletion leaves the old state
in place, so the same `sync_diff.to_delete` is detected again on the next run.

## Testing

Add focused regression coverage:

- dry run reports deletes but does not call deletion or insert helpers;
- `apply=true` calls deletion before `ainsert_custom_kg`;
- deletion failure prevents state writes;
- `ainsert_custom_kg` writes provenance indexes needed for later deletion;
- deleting one source row removes only that row's chunk contribution and
  preserves shared entities/relations with remaining sources.

## Documentation

After implementation, update the audit deployment/API notes to document:

- database sync deletion is supported through `/audit/kg-sync`;
- document deletion still uses the existing document delete endpoints;
- state file durability matters because it is required for incremental delete
  detection and retry.
