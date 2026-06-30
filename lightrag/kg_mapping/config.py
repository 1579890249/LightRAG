"""Mapping configuration loading for database-backed custom KG ingestion."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


@dataclass(frozen=True)
class MappingConfig:
    """Parsed mapping configuration.

    The configuration is intentionally dictionary-backed so customer-specific
    graph concepts and table schemas can evolve without adding Python classes
    for every business concept.
    """

    schema_version: str
    database_name: str
    sources: dict[str, dict[str, Any]]
    entity_types: dict[str, dict[str, Any]]
    entities: list[dict[str, Any]]
    relationships: list[dict[str, Any]]
    raw: dict[str, Any]


def load_mapping_config(config: str | Path | dict[str, Any]) -> MappingConfig:
    """Load a mapping config from a path, YAML string, or dictionary."""

    if isinstance(config, Path):
        data = _load_yaml_file(config)
    elif isinstance(config, str):
        path = Path(config)
        data = _load_yaml_file(path) if path.exists() else yaml.safe_load(config)
    else:
        data = dict(config)

    if not isinstance(data, dict):
        raise ValueError("KG mapping config must be a YAML object")

    schema_version = _require_string(data, "schema_version")
    database_name = str(data.get("database_name") or "default")
    sources = _require_dict(data, "sources")
    entity_types = _require_dict(data, "entity_types")
    entities = _require_list(data, "entities")
    relationships = data.get("relationships", [])
    if not isinstance(relationships, list):
        raise ValueError("KG mapping config field 'relationships' must be a list")

    _validate_sources(sources)
    _validate_entities(entities, sources, entity_types)
    _validate_relationships(relationships, sources, entity_types)

    return MappingConfig(
        schema_version=schema_version,
        database_name=database_name,
        sources=sources,
        entity_types=entity_types,
        entities=entities,
        relationships=relationships,
        raw=data,
    )


def _load_yaml_file(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as file:
        data = yaml.safe_load(file)
    if not isinstance(data, dict):
        raise ValueError(f"KG mapping config file must contain a YAML object: {path}")
    return data


def _require_string(data: dict[str, Any], field_name: str) -> str:
    value = data.get(field_name)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"KG mapping config field '{field_name}' is required")
    return value.strip()


def _require_dict(data: dict[str, Any], field_name: str) -> dict[str, Any]:
    value = data.get(field_name)
    if not isinstance(value, dict) or not value:
        raise ValueError(f"KG mapping config field '{field_name}' must be a non-empty object")
    return value


def _require_list(data: dict[str, Any], field_name: str) -> list[dict[str, Any]]:
    value = data.get(field_name)
    if not isinstance(value, list) or not value:
        raise ValueError(f"KG mapping config field '{field_name}' must be a non-empty list")
    if not all(isinstance(item, dict) for item in value):
        raise ValueError(f"KG mapping config field '{field_name}' must contain objects")
    return value


def _validate_sources(sources: dict[str, dict[str, Any]]) -> None:
    for source_name, source_config in sources.items():
        if not isinstance(source_config, dict):
            raise ValueError(f"Source '{source_name}' config must be an object")
        primary_key = source_config.get("primary_key")
        if not isinstance(primary_key, str) or not primary_key.strip():
            raise ValueError(f"Source '{source_name}' requires primary_key")


def _validate_entities(
    entities: list[dict[str, Any]],
    sources: dict[str, dict[str, Any]],
    entity_types: dict[str, dict[str, Any]],
) -> None:
    for entity_config in entities:
        source_name = entity_config.get("source")
        if source_name not in sources:
            raise ValueError(f"Entity mapping references unknown source '{source_name}'")
        entity_type = entity_config.get("entity_type")
        if entity_type not in entity_types:
            raise ValueError(f"Entity mapping references unknown entity_type '{entity_type}'")
        if not entity_config.get("id_field"):
            raise ValueError(f"Entity mapping for '{entity_type}' requires id_field")


def _validate_relationships(
    relationships: list[dict[str, Any]],
    sources: dict[str, dict[str, Any]],
    entity_types: dict[str, dict[str, Any]],
) -> None:
    for relation_config in relationships:
        source_name = relation_config.get("source")
        if source_name not in sources:
            raise ValueError(f"Relationship mapping references unknown source '{source_name}'")
        for endpoint_name in ("src", "tgt"):
            endpoint = relation_config.get(endpoint_name)
            if not isinstance(endpoint, dict):
                raise ValueError(
                    f"Relationship '{relation_config.get('relation_type')}' "
                    f"requires {endpoint_name} endpoint"
                )
            entity_type = endpoint.get("entity_type")
            if entity_type not in entity_types:
                raise ValueError(
                    f"Relationship endpoint references unknown entity_type '{entity_type}'"
                )
            if not endpoint.get("id_field"):
                raise ValueError(
                    f"Relationship endpoint '{endpoint_name}' requires id_field"
                )
