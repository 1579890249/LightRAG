"""Build LightRAG custom_kg payloads from generic mapping configs."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any

from .config import MappingConfig
from lightrag.utils import compute_mdhash_id


@dataclass(frozen=True)
class KGBuildResult:
    custom_kg: dict[str, list[dict[str, Any]]]
    sync_records: list[dict[str, Any]]


class ConfigurableKGBuilder:
    """Convert source rows into LightRAG's custom_kg shape."""

    def __init__(self, config: MappingConfig) -> None:
        self.config = config

    def build(self, rows_by_source: dict[str, list[dict[str, Any]]]) -> KGBuildResult:
        chunks: list[dict[str, Any]] = []
        entities_by_name: dict[str, dict[str, Any]] = {}
        relationships_by_key: dict[tuple[str, str, str], dict[str, Any]] = {}
        sync_records: list[dict[str, Any]] = []
        entity_name_index = _build_entity_name_index(
            self.config,
            rows_by_source,
        )
        entity_display_name_index = _build_entity_display_name_index(
            self.config,
            rows_by_source,
        )
        source_context = _build_source_context(rows_by_source)

        for source_name in self.config.sources:
            source_rows = rows_by_source.get(source_name, [])
            source_config = self.config.sources[source_name]
            primary_key_field = source_config["primary_key"]
            entity_configs = self._entity_mappings_for_source(source_name)
            relation_configs = self._relationship_mappings_for_source(source_name)
            enrich_relationship_chunks = not entity_configs and bool(relation_configs)

            for row in source_rows:
                primary_key = _stringify(row.get(primary_key_field))
                if not primary_key:
                    raise ValueError(
                        f"Source '{source_name}' row is missing primary key "
                        f"field '{primary_key_field}'"
                    )

                source_id = _source_id(
                    self.config.database_name,
                    source_name,
                    primary_key,
                )
                row_entities: list[str] = []
                row_relationships: list[dict[str, str]] = []
                row_chunk_fields: dict[str, str] = {}
                excluded_chunk_fields: set[str] = set()
                priority_chunk_fields: list[str] = []
                if enrich_relationship_chunks:
                    excluded_chunk_fields = _relationship_endpoint_id_fields(
                        relation_configs
                    )
                    priority_chunk_fields = _relationship_chunk_priority_fields(
                        relation_configs,
                    )

                for entity_config in entity_configs:
                    entity = self._build_entity(entity_config, row, source_id)
                    if entity is None:
                        continue
                    entity_name = entity["entity_name"]
                    entities_by_name[entity_name] = entity
                    row_entities.append(entity_name)

                for relation_config in relation_configs:
                    relation = self._build_relationship(
                        relation_config,
                        row,
                        source_id,
                        entity_name_index,
                        entity_display_name_index,
                    )
                    if relation is None:
                        continue
                    relation_key = (
                        relation["src_id"],
                        relation["tgt_id"],
                        relation["keywords"],
                    )
                    relationships_by_key[relation_key] = relation
                    row_relationships.append(
                        {
                            "src_id": relation["src_id"],
                            "tgt_id": relation["tgt_id"],
                            "keywords": relation["keywords"],
                        }
                    )
                    if enrich_relationship_chunks:
                        _merge_chunk_fields(
                            row_chunk_fields,
                            _relationship_chunk_fields(
                                row,
                                relation_config,
                                entity_display_name_index,
                                relation["description"],
                                source_context,
                            ),
                        )

                chunk_content = _row_chunk_content(
                    source_name,
                    row,
                    row_chunk_fields,
                    excluded_fields=excluded_chunk_fields,
                    priority_fields=priority_chunk_fields,
                )
                chunk_id = compute_mdhash_id(chunk_content, prefix="chunk-")
                chunks.append(
                    {
                        "source_id": source_id,
                        "content": chunk_content,
                        "file_path": source_id,
                    }
                )

                sync_records.append(
                    {
                        "source": source_name,
                        "primary_key": primary_key,
                        "source_id": source_id,
                        "row_hash": _sync_record_hash(
                            row,
                            row_entities,
                            row_relationships,
                            [source_id],
                            [chunk_id],
                        ),
                        "entities": sorted(set(row_entities)),
                        "relationships": row_relationships,
                        "chunks": [source_id],
                        "chunk_ids": [chunk_id],
                    }
                )

        return KGBuildResult(
            custom_kg={
                "chunks": chunks,
                "entities": list(entities_by_name.values()),
                "relationships": list(relationships_by_key.values()),
            },
            sync_records=sync_records,
        )

    def _entity_mappings_for_source(self, source_name: str) -> list[dict[str, Any]]:
        return [
            entity_config
            for entity_config in self.config.entities
            if entity_config["source"] == source_name
        ]

    def _relationship_mappings_for_source(
        self,
        source_name: str,
    ) -> list[dict[str, Any]]:
        return [
            relation_config
            for relation_config in self.config.relationships
            if relation_config["source"] == source_name
        ]

    def _build_entity(
        self,
        entity_config: dict[str, Any],
        row: dict[str, Any],
        source_id: str,
    ) -> dict[str, Any] | None:
        raw_id = _stringify(row.get(entity_config["id_field"]))
        if not raw_id:
            return None

        entity_type = entity_config["entity_type"]
        entity_name = _entity_graph_name(self.config, entity_config, row, raw_id)
        description = _render_description(entity_config, row)
        metadata = _metadata_for_config(entity_config, row)
        if metadata:
            description = _append_metadata(description, metadata)

        return {
            "entity_name": entity_name,
            "entity_type": entity_type,
            "description": description or entity_name,
            "source_id": source_id,
            "file_path": source_id,
        }

    def _build_relationship(
        self,
        relation_config: dict[str, Any],
        row: dict[str, Any],
        source_id: str,
        entity_name_index: dict[tuple[str, str], str],
        entity_display_name_index: dict[tuple[str, str], str],
    ) -> dict[str, Any] | None:
        src_id = _endpoint_name(
            self.config,
            relation_config["src"],
            row,
            entity_name_index,
        )
        tgt_id = _endpoint_name(
            self.config,
            relation_config["tgt"],
            row,
            entity_name_index,
        )
        if not src_id or not tgt_id:
            return None

        relation_type = relation_config["relation_type"]
        template_row = _relationship_template_row(
            row,
            relation_config,
            entity_display_name_index,
        )
        description = _render_template(
            relation_config.get("description_template"),
            template_row,
        )
        if not description:
            description = f"{src_id} {relation_type} {tgt_id}."

        return {
            "src_id": src_id,
            "tgt_id": tgt_id,
            "keywords": relation_type,
            "description": description,
            "source_id": source_id,
            "weight": float(relation_config.get("weight", 1.0)),
            "file_path": source_id,
        }


def _entity_name(config: MappingConfig, entity_type: str, raw_id: str) -> str:
    type_config = config.entity_types.get(entity_type, {})
    prefix = str(type_config.get("id_prefix") or entity_type)
    return f"{prefix}:{raw_id}"


def _entity_graph_name(
    config: MappingConfig,
    entity_config: dict[str, Any],
    row: dict[str, Any],
    raw_id: str,
) -> str:
    templated = _render_template(entity_config.get("entity_name_template"), row)
    if templated:
        return templated
    return _entity_name(config, entity_config["entity_type"], raw_id)


def _endpoint_name(
    config: MappingConfig,
    endpoint_config: dict[str, Any],
    row: dict[str, Any],
    entity_name_index: dict[tuple[str, str], str] | None = None,
) -> str | None:
    raw_id = _stringify(row.get(endpoint_config["id_field"]))
    if not raw_id:
        return None
    entity_type = endpoint_config["entity_type"]
    if entity_name_index:
        indexed_name = entity_name_index.get((entity_type, raw_id))
        if indexed_name:
            return indexed_name
    return _entity_name(config, entity_type, raw_id)


def _build_entity_name_index(
    config: MappingConfig,
    rows_by_source: dict[str, list[dict[str, Any]]],
) -> dict[tuple[str, str], str]:
    index: dict[tuple[str, str], str] = {}
    for entity_config in config.entities:
        source_name = entity_config["source"]
        entity_type = entity_config["entity_type"]
        id_field = entity_config["id_field"]
        for row in rows_by_source.get(source_name, []):
            raw_id = _stringify(row.get(id_field))
            if not raw_id:
                continue
            index[(entity_type, raw_id)] = _entity_graph_name(
                config,
                entity_config,
                row,
                raw_id,
            )
    return index


def _build_entity_display_name_index(
    config: MappingConfig,
    rows_by_source: dict[str, list[dict[str, Any]]],
) -> dict[tuple[str, str], str]:
    index: dict[tuple[str, str], str] = {}
    for entity_config in config.entities:
        source_name = entity_config["source"]
        entity_type = entity_config["entity_type"]
        id_field = entity_config["id_field"]
        for row in rows_by_source.get(source_name, []):
            raw_id = _stringify(row.get(id_field))
            if not raw_id:
                continue
            display_name = _entity_display_name(entity_config, row)
            if display_name:
                index[(entity_type, raw_id)] = display_name
    return index


def _entity_display_name(entity_config: dict[str, Any], row: dict[str, Any]) -> str:
    name = _render_template(entity_config.get("name_template"), row)
    if name:
        return name

    name_field = entity_config.get("name_field")
    if name_field:
        return _stringify(row.get(name_field))

    return _stringify(row.get(entity_config["id_field"]))


def _relationship_template_row(
    row: dict[str, Any],
    relation_config: dict[str, Any],
    entity_name_index: dict[tuple[str, str], str],
) -> dict[str, Any]:
    template_row = dict(row)
    for endpoint_alias, endpoint_config in (
        ("src", relation_config["src"]),
        ("tgt", relation_config["tgt"]),
    ):
        id_field = endpoint_config["id_field"]
        entity_type = endpoint_config["entity_type"]
        raw_id = _stringify(row.get(id_field))
        display_name = entity_name_index.get((entity_type, raw_id), raw_id)
        template_row[f"{id_field}_name"] = display_name
        template_row[f"{endpoint_alias}_name"] = display_name
    return template_row


def _source_id(database_name: str, source_name: str, primary_key: str) -> str:
    return f"db://{database_name}/{source_name}/{primary_key}"


def _render_description(entity_config: dict[str, Any], row: dict[str, Any]) -> str:
    templated = _render_template(entity_config.get("description_template"), row)
    if templated:
        return templated

    name = _render_template(entity_config.get("name_template"), row)
    if name:
        return name

    name_field = entity_config.get("name_field")
    if name_field:
        return _stringify(row.get(name_field))

    return ""


def _render_template(template: Any, row: dict[str, Any]) -> str:
    if not template:
        return ""
    try:
        return str(template).format_map(_SafeFormatRow(row)).strip()
    except KeyError as exc:
        raise ValueError(f"Missing field for template rendering: {exc}") from exc


class _SafeFormatRow(dict):
    def __init__(self, row: dict[str, Any]) -> None:
        super().__init__({key: _stringify(value) for key, value in row.items()})

    def __missing__(self, key: str) -> str:
        raise KeyError(key)


def _metadata_for_config(
    entity_config: dict[str, Any],
    row: dict[str, Any],
) -> dict[str, str]:
    metadata_fields = entity_config.get("metadata_fields", [])
    if metadata_fields == "*":
        selected = row
    elif isinstance(metadata_fields, list):
        selected = {
            field_name: row.get(field_name)
            for field_name in metadata_fields
            if field_name in row
        }
    else:
        raise ValueError("metadata_fields must be '*' or a list of field names")
    return {
        key: _stringify(value)
        for key, value in sorted(selected.items())
        if _stringify(value)
    }


def _append_metadata(description: str, metadata: dict[str, str]) -> str:
    metadata_text = "; ".join(f"{key}={value}" for key, value in metadata.items())
    if not description:
        return metadata_text
    return f"{description}; {metadata_text}"


def _relationship_chunk_fields(
    row: dict[str, Any],
    relation_config: dict[str, Any],
    entity_display_name_index: dict[tuple[str, str], str],
    description: str,
    source_context: dict[str, Any] | None = None,
) -> dict[str, str]:
    template_row = _relationship_template_row(
        row,
        relation_config,
        entity_display_name_index,
    )
    fields: dict[str, str] = {}
    for endpoint_config in (relation_config["src"], relation_config["tgt"]):
        id_field = endpoint_config["id_field"]
        display_name = _stringify(template_row.get(f"{id_field}_name"))
        if display_name:
            fields[f"{id_field}_name"] = display_name
    if description:
        fields["relationship"] = description
    if source_context:
        _merge_chunk_fields(
            fields,
            _relationship_context_chunk_fields(
                row,
                relation_config,
                template_row,
                source_context,
            ),
        )
    return fields


def _relationship_endpoint_id_fields(
    relation_configs: list[dict[str, Any]],
) -> set[str]:
    fields: set[str] = set()
    for relation_config in relation_configs:
        for endpoint_name in ("src", "tgt"):
            endpoint_config = relation_config.get(endpoint_name, {})
            id_field = _stringify(endpoint_config.get("id_field"))
            if id_field:
                fields.add(id_field)
    return fields


def _relationship_chunk_priority_fields(
    relation_configs: list[dict[str, Any]],
) -> list[str]:
    fields = ["relationship_context", "relationship"]
    for relation_config in relation_configs:
        for endpoint_name in ("src", "tgt"):
            endpoint_config = relation_config.get(endpoint_name, {})
            id_field = _stringify(endpoint_config.get("id_field"))
            if not id_field:
                continue
            fields.extend(
                [
                    f"{id_field}_name",
                    f"{id_field}_enterprise_name",
                    f"{id_field}_position",
                ]
            )
    fields.extend(["relation_type", "common_project_names", "id"])
    return list(dict.fromkeys(fields))


def _build_source_context(rows_by_source: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
    person_positions: dict[str, dict[str, str]] = {}
    for row in rows_by_source.get("person_enterprise_position", []):
        person_id = _stringify(row.get("person_id"))
        if not person_id:
            continue
        if person_id in person_positions and _stringify(row.get("status")) != "1":
            continue
        position = {
            "enterprise_id": _stringify(row.get("enterprise_id")),
            "enterprise_name": _stringify(row.get("enterprise_name")),
            "position": _stringify(row.get("position")),
        }
        if not position["enterprise_name"]:
            position["enterprise_name"] = position["enterprise_id"]
        person_positions[person_id] = position

    project_names = {
        _stringify(row.get("project_id")): _stringify(row.get("project_name"))
        for row in rows_by_source.get("project", [])
        if _stringify(row.get("project_id"))
    }

    enterprise_projects: dict[str, set[str]] = {}
    for row in rows_by_source.get("bid_record", []):
        enterprise_id = _stringify(row.get("enterprise_id"))
        project_id = _stringify(row.get("project_id"))
        if enterprise_id and project_id:
            enterprise_projects.setdefault(enterprise_id, set()).add(project_id)

    return {
        "person_positions": person_positions,
        "project_names": project_names,
        "enterprise_projects": enterprise_projects,
    }


def _relationship_context_chunk_fields(
    row: dict[str, Any],
    relation_config: dict[str, Any],
    template_row: dict[str, Any],
    source_context: dict[str, Any],
) -> dict[str, str]:
    src = relation_config["src"]
    tgt = relation_config["tgt"]
    if src.get("entity_type") != "Person" or tgt.get("entity_type") != "Person":
        return {}

    src_id_field = src["id_field"]
    tgt_id_field = tgt["id_field"]
    src_person_id = _stringify(row.get(src_id_field))
    tgt_person_id = _stringify(row.get(tgt_id_field))
    positions = source_context.get("person_positions", {})
    src_position = positions.get(src_person_id, {})
    tgt_position = positions.get(tgt_person_id, {})
    fields: dict[str, str] = {}

    for prefix, position in (
        (src_id_field, src_position),
        (tgt_id_field, tgt_position),
    ):
        for key in ("enterprise_id", "enterprise_name", "position"):
            value = _stringify(position.get(key))
            if value:
                fields[f"{prefix}_{key}"] = value

    src_enterprise_id = _stringify(src_position.get("enterprise_id"))
    tgt_enterprise_id = _stringify(tgt_position.get("enterprise_id"))
    common_project_names = _common_project_names(
        src_enterprise_id,
        tgt_enterprise_id,
        source_context,
    )
    if common_project_names:
        fields["common_project_names"] = "、".join(common_project_names)

    has_context_details = bool(fields)
    src_name = _stringify(template_row.get(f"{src_id_field}_name")) or src_person_id
    tgt_name = _stringify(template_row.get(f"{tgt_id_field}_name")) or tgt_person_id
    relation_type = _stringify(row.get("relation_type"))
    context = _person_relation_context_sentence(
        src_name,
        src_position,
        tgt_name,
        tgt_position,
        relation_type,
        common_project_names,
    )
    if has_context_details and context:
        fields["relationship_context"] = context

    return fields


def _common_project_names(
    src_enterprise_id: str,
    tgt_enterprise_id: str,
    source_context: dict[str, Any],
) -> list[str]:
    if not src_enterprise_id or not tgt_enterprise_id:
        return []
    enterprise_projects = source_context.get("enterprise_projects", {})
    common_project_ids = sorted(
        set(enterprise_projects.get(src_enterprise_id, set()))
        & set(enterprise_projects.get(tgt_enterprise_id, set()))
    )
    project_names = source_context.get("project_names", {})
    return [
        _stringify(project_names.get(project_id)) or project_id
        for project_id in common_project_ids
    ]


def _person_relation_context_sentence(
    src_name: str,
    src_position: dict[str, str],
    tgt_name: str,
    tgt_position: dict[str, str],
    relation_type: str,
    common_project_names: list[str],
) -> str:
    if not src_name or not tgt_name:
        return ""
    relation_text = f"{src_name}{_person_affiliation_text(src_position)}与{tgt_name}{_person_affiliation_text(tgt_position)}"
    if relation_type:
        relation_text = f"{relation_text}存在人员关系：{relation_type}"
    else:
        relation_text = f"{relation_text}存在人员关系"
    if common_project_names:
        relation_text = (
            f"{relation_text}；双方企业共同参与投标项目："
            f"{'、'.join(common_project_names)}"
        )
    return f"{relation_text}。"


def _person_affiliation_text(position: dict[str, str]) -> str:
    parts = [
        _stringify(position.get("enterprise_name")),
        _stringify(position.get("position")),
    ]
    parts = [part for part in parts if part]
    if not parts:
        return ""
    return f"（{'，'.join(parts)}）"


def _merge_chunk_fields(
    target: dict[str, str],
    fields: dict[str, str],
) -> None:
    for key, value in fields.items():
        clean_value = _stringify(value)
        if not clean_value:
            continue
        if not _stringify(target.get(key)):
            target[key] = clean_value
            continue

        suffix = 2
        candidate_key = f"{key}_{suffix}"
        while _stringify(target.get(candidate_key)):
            suffix += 1
            candidate_key = f"{key}_{suffix}"
        target[candidate_key] = clean_value


def _row_chunk_content(
    source_name: str,
    row: dict[str, Any],
    extra_fields: dict[str, Any] | None = None,
    *,
    excluded_fields: set[str] | None = None,
    priority_fields: list[str] | None = None,
) -> str:
    excluded_fields = excluded_fields or set()
    chunk_fields = {
        key: value for key, value in row.items() if key not in excluded_fields
    }
    for key, value in (extra_fields or {}).items():
        if _stringify(value) and not _stringify(chunk_fields.get(key)):
            chunk_fields[key] = value

    ordered_keys: list[str] = []
    for key in priority_fields or []:
        if key in chunk_fields and _stringify(chunk_fields.get(key)):
            ordered_keys.append(key)
    ordered_key_set = set(ordered_keys)
    ordered_keys.extend(
        key
        for key in sorted(chunk_fields)
        if key not in ordered_key_set
    )

    fields = "; ".join(
        f"{key}={_stringify(chunk_fields[key])}" for key in ordered_keys
    )
    return f"source={source_name}; {fields}"


def _stable_hash(row: dict[str, Any]) -> str:
    encoded = json.dumps(
        {key: _stringify(value) for key, value in sorted(row.items())},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _sync_record_hash(
    row: dict[str, Any],
    entity_names: list[str],
    relationships: list[dict[str, str]],
    chunks: list[str],
    chunk_ids: list[str],
) -> str:
    encoded = json.dumps(
        {
            "row": {key: _stringify(value) for key, value in sorted(row.items())},
            "entities": sorted(set(entity_names)),
            "relationships": sorted(
                {
                    (
                        relationship["src_id"],
                        relationship["tgt_id"],
                        relationship["keywords"],
                    )
                    for relationship in relationships
                }
            ),
            "chunks": sorted(chunks),
            "chunk_ids": sorted(chunk_ids),
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _stringify(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()
