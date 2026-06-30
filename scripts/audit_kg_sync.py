"""Build a configurable audit custom_kg payload from database tables.

This POC entrypoint is intentionally dry-run oriented: it reads business tables,
builds the LightRAG custom_kg payload, computes row-level sync differences, and
optionally writes JSON artifacts for review. Applying those changes to a live
LightRAG workspace is a separate deployment step.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from lightrag.kg_mapping import (
    ConfigurableKGBuilder,
    ConfiguredSQLSource,
    apply_custom_kg,
    diff_sync_records,
    load_mapping_config,
)


def main() -> int:
    args = _parse_args()
    mapping_config = load_mapping_config(args.mapping)
    rows_by_source = ConfiguredSQLSource(args.connection_url, mapping_config).load()
    result = ConfigurableKGBuilder(mapping_config).build(rows_by_source)

    previous_records = _read_json_list(args.state) if args.state else []
    sync_diff = diff_sync_records(previous_records, result.sync_records)

    summary = {
        "schema_version": mapping_config.schema_version,
        "database_name": mapping_config.database_name,
        "sources": {
            source_name: len(rows)
            for source_name, rows in rows_by_source.items()
        },
        "custom_kg": {
            "chunks": len(result.custom_kg["chunks"]),
            "entities": len(result.custom_kg["entities"]),
            "relationships": len(result.custom_kg["relationships"]),
        },
        "sync_diff": {
            "insert": len(sync_diff.to_insert),
            "update": len(sync_diff.to_update),
            "delete": len(sync_diff.to_delete),
            "unchanged": len(sync_diff.unchanged),
        },
        "applied": False,
    }

    print(json.dumps(summary, ensure_ascii=False, indent=2))

    if args.apply:
        apply_result = asyncio.run(
            apply_custom_kg(result.custom_kg, workspace=args.workspace)
        )
        summary["applied"] = True
        summary["apply_result"] = {
            "inserted_chunks": apply_result.inserted_chunks,
            "inserted_entities": apply_result.inserted_entities,
            "inserted_relationships": apply_result.inserted_relationships,
        }
        print(json.dumps({"apply_result": summary["apply_result"]}, ensure_ascii=False, indent=2))

    if args.output:
        _write_json(
            args.output,
            {
                "custom_kg": result.custom_kg,
                "sync_records": result.sync_records,
                "sync_diff": {
                    "to_insert": sync_diff.to_insert,
                    "to_update": sync_diff.to_update,
                    "update_previous": sync_diff.update_previous,
                    "to_delete": sync_diff.to_delete,
                    "unchanged": sync_diff.unchanged,
                },
                "summary": summary,
            },
        )

    if args.write_state:
        if not args.state:
            raise SystemExit("--write-state requires --state")
        _write_json(args.state, result.sync_records)

    return 0


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build audit database rows into a LightRAG custom_kg payload."
    )
    parser.add_argument(
        "--mapping",
        required=True,
        help="Path to the KG mapping YAML file.",
    )
    parser.add_argument(
        "--connection-url",
        required=True,
        help=(
            "Database URL, e.g. sqlite:///audit.db or "
            "postgresql://rag:rag@172.16.1.203:5432/audit"
        ),
    )
    parser.add_argument(
        "--state",
        help="Optional JSON file containing previous sync_records.",
    )
    parser.add_argument(
        "--write-state",
        action="store_true",
        help="Write current sync_records to --state after a successful dry run.",
    )
    parser.add_argument(
        "--output",
        help="Optional JSON file to write generated custom_kg and sync diff.",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        help="Insert the generated custom_kg into the configured LightRAG workspace.",
    )
    parser.add_argument(
        "--workspace",
        help="Override the LightRAG workspace used by --apply.",
    )
    return parser.parse_args()


def _read_json_list(path_value: str | None) -> list[dict[str, Any]]:
    if not path_value:
        return []
    path = Path(path_value)
    if not path.exists():
        return []
    with path.open("r", encoding="utf-8") as file:
        data = json.load(file)
    if not isinstance(data, list) or not all(isinstance(item, dict) for item in data):
        raise ValueError(f"Sync state must be a JSON list of objects: {path}")
    return data


def _write_json(path_value: str, data: Any) -> None:
    path = Path(path_value)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as file:
        json.dump(data, file, ensure_ascii=False, indent=2)
        file.write("\n")


if __name__ == "__main__":
    raise SystemExit(main())
