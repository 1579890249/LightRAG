"""Sync-state helpers for database-backed KG ingestion."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class SyncDiff:
    to_insert: list[dict[str, Any]]
    to_update: list[dict[str, Any]]
    to_delete: list[dict[str, Any]]
    unchanged: list[dict[str, Any]]
    update_previous: list[dict[str, Any]] = field(default_factory=list)


def diff_sync_records(
    previous: list[dict[str, Any]],
    current: list[dict[str, Any]],
) -> SyncDiff:
    """Classify source-row sync records by source + primary key identity."""

    previous_by_key = {_identity(record): record for record in previous}
    current_by_key = {_identity(record): record for record in current}

    to_insert: list[dict[str, Any]] = []
    to_update: list[dict[str, Any]] = []
    to_delete: list[dict[str, Any]] = []
    unchanged: list[dict[str, Any]] = []
    update_previous: list[dict[str, Any]] = []

    for key in sorted(current_by_key):
        current_record = current_by_key[key]
        previous_record = previous_by_key.get(key)
        if previous_record is None:
            to_insert.append(current_record)
        elif previous_record.get("row_hash") != current_record.get("row_hash"):
            to_update.append(current_record)
            update_previous.append(previous_record)
        else:
            unchanged.append(current_record)

    for key in sorted(previous_by_key):
        if key not in current_by_key:
            to_delete.append(previous_by_key[key])

    return SyncDiff(
        to_insert=to_insert,
        to_update=to_update,
        to_delete=to_delete,
        unchanged=unchanged,
        update_previous=update_previous,
    )


def _identity(record: dict[str, Any]) -> tuple[str, str]:
    return (str(record["source"]), str(record["primary_key"]))
