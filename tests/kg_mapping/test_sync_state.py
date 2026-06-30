from lightrag.kg_mapping import diff_sync_records


def test_diff_sync_records_classifies_inserts_updates_and_deletes():
    previous = [
        {
            "source": "enterprise",
            "primary_key": "E001",
            "row_hash": "old-hash",
            "entities": ["Organization:E001"],
            "relationships": [],
            "chunks": ["db://audit/enterprise/E001"],
        },
        {
            "source": "enterprise",
            "primary_key": "E999",
            "row_hash": "deleted-hash",
            "entities": ["Organization:E999"],
            "relationships": [
                {
                    "src_id": "Person:P999",
                    "tgt_id": "Organization:E999",
                    "keywords": "EMPLOYED_BY",
                }
            ],
            "chunks": ["db://audit/enterprise/E999"],
        },
    ]
    current = [
        {
            "source": "enterprise",
            "primary_key": "E001",
            "row_hash": "new-hash",
            "entities": ["Organization:E001"],
            "relationships": [],
            "chunks": ["db://audit/enterprise/E001"],
        },
        {
            "source": "enterprise",
            "primary_key": "E002",
            "row_hash": "inserted-hash",
            "entities": ["Organization:E002"],
            "relationships": [],
            "chunks": ["db://audit/enterprise/E002"],
        },
    ]

    diff = diff_sync_records(previous, current)

    assert [item["primary_key"] for item in diff.to_insert] == ["E002"]
    assert [item["primary_key"] for item in diff.to_update] == ["E001"]
    assert [item["primary_key"] for item in diff.to_delete] == ["E999"]
    assert diff.unchanged == []


def test_diff_sync_records_uses_source_and_primary_key_as_identity():
    previous = [
        {
            "source": "enterprise",
            "primary_key": "1",
            "row_hash": "hash-a",
            "entities": ["Organization:1"],
            "relationships": [],
            "chunks": ["db://audit/enterprise/1"],
        }
    ]
    current = [
        {
            "source": "person",
            "primary_key": "1",
            "row_hash": "hash-a",
            "entities": ["Person:1"],
            "relationships": [],
            "chunks": ["db://audit/person/1"],
        }
    ]

    diff = diff_sync_records(previous, current)

    assert [item["source"] for item in diff.to_insert] == ["person"]
    assert [item["source"] for item in diff.to_delete] == ["enterprise"]
