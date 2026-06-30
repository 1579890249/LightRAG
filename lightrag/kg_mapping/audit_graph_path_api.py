"""Audit graph multi-hop path query API."""

from __future__ import annotations

import asyncio
from collections import deque
from dataclasses import dataclass
from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from lightrag.utils import logger

from .audit_graph_config import (
    AuditGraphRuleConfigError,
    build_builtin_graph_config_for_rule_type,
    load_active_graph_configs_for_rule_type,
    upsert_graph_config_for_rules,
)
from .audit_rule_api import DEFAULT_AUDIT_DB_URL
from .config import load_mapping_config


DEFAULT_AUDIT_MAPPING_PATH = "configs/kg_mappings/audit_customer_ys.yaml"
MAX_ALLOWED_DEPTH = 6


class GraphPathEndpoint(BaseModel):
    name: str = Field(min_length=1)


class GraphPathQueryRequest(BaseModel):
    start: GraphPathEndpoint
    end: GraphPathEndpoint
    business_type: str = Field(min_length=1)
    max_depth: int | None = Field(default=None, ge=1, le=MAX_ALLOWED_DEPTH)
    limit: int = Field(default=50, ge=1, le=200)
    mapping: str | None = None
    connection_url: str | None = None


class GraphRuleConfigUpsertRequest(BaseModel):
    rule_type: str = Field(min_length=1)
    rule_name: str | None = Field(default=None, min_length=1)
    rule_id: int | None = Field(default=None, gt=0)
    default_max_depth: int | None = Field(default=None, ge=1, le=MAX_ALLOWED_DEPTH)
    config: dict[str, Any] | None = None
    basis_hash: str | None = None
    mapping: str | None = None
    connection_url: str | None = None


@dataclass(frozen=True)
class _TraversalConfig:
    relation_types: set[str]
    allowed_entity_types: set[str]
    max_depth: int


def create_audit_graph_path_router(
    rag,
    *,
    connection_url: str = DEFAULT_AUDIT_DB_URL,
    mapping_path: str = DEFAULT_AUDIT_MAPPING_PATH,
    auth_dependency=None,
) -> APIRouter:
    router = APIRouter(tags=["audit-graph-path"])
    dependencies = [Depends(auth_dependency)] if auth_dependency is not None else []

    @router.post("/audit/graph/paths/query", dependencies=dependencies)
    async def query_graph_paths(request: GraphPathQueryRequest):
        effective_connection_url = request.connection_url or connection_url
        effective_mapping = request.mapping or mapping_path
        try:
            mapping_config = load_mapping_config(effective_mapping)
            rule_configs = await asyncio.to_thread(
                load_active_graph_configs_for_rule_type,
                effective_connection_url,
                request.business_type,
                mapping_config,
            )
        except AuditGraphRuleConfigError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        if not rule_configs:
            raise HTTPException(
                status_code=404,
                detail=(
                    "No active graph path config found for business_type "
                    f"'{request.business_type}'"
                ),
            )

        traversal_config = _merge_rule_configs(rule_configs, request.max_depth)
        graph = getattr(rag, "chunk_entity_relation_graph", None)
        if graph is None:
            raise HTTPException(status_code=500, detail="Graph storage is not configured")

        start_node = await graph.get_node(request.start.name)
        end_node = await graph.get_node(request.end.name)
        if start_node is None:
            raise HTTPException(
                status_code=404,
                detail=f"Start node '{request.start.name}' was not found in graph",
            )
        if end_node is None:
            raise HTTPException(
                status_code=404,
                detail=f"End node '{request.end.name}' was not found in graph",
            )

        if not _node_type_allowed(start_node, traversal_config.allowed_entity_types):
            raise HTTPException(
                status_code=400,
                detail=(
                    f"Start node '{request.start.name}' entity_type "
                    f"'{start_node.get('entity_type')}' is not allowed by graph config"
                ),
            )
        if not _node_type_allowed(end_node, traversal_config.allowed_entity_types):
            raise HTTPException(
                status_code=400,
                detail=(
                    f"End node '{request.end.name}' entity_type "
                    f"'{end_node.get('entity_type')}' is not allowed by graph config"
                ),
            )

        paths = await _find_paths_optimized(
            graph,
            request.start.name,
            request.end.name,
            traversal_config,
            request.limit,
        )

        return {
            "business_type": request.business_type,
            "start": _node_response(request.start.name, start_node),
            "end": _node_response(request.end.name, end_node),
            "max_depth": traversal_config.max_depth,
            "relation_types": sorted(traversal_config.relation_types),
            "allowed_entity_types": sorted(traversal_config.allowed_entity_types),
            "rules": [
                {
                    "rule_id": row["rule_id"],
                    "rule_name": row["rule_name"],
                    "rule_basis": row["rule_basis"],
                    "rule_type": row["rule_type"],
                    "config_id": row["config_id"],
                }
                for row in rule_configs
            ],
            "paths": paths,
        }

    @router.post("/audit/graph/rule-configs/upsert", dependencies=dependencies)
    async def upsert_graph_rule_configs(request: GraphRuleConfigUpsertRequest):
        effective_connection_url = request.connection_url or connection_url
        effective_mapping = request.mapping or mapping_path
        try:
            mapping_config = load_mapping_config(effective_mapping)
            source = "explicit_config" if request.config is not None else "builtin_template"
            config = (
                validate_request_config(request.config, mapping_config)
                if request.config is not None
                else build_builtin_graph_config_for_rule_type(
                    request.rule_type,
                    mapping_config,
                    request.default_max_depth,
                )
            )
            configs = await asyncio.to_thread(
                upsert_graph_config_for_rules,
                effective_connection_url,
                rule_type=request.rule_type,
                rule_name=request.rule_name,
                rule_id=request.rule_id,
                config=config,
                mapping_config=mapping_config,
                basis_hash=request.basis_hash,
            )
        except AuditGraphRuleConfigError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

        if not configs:
            raise HTTPException(
                status_code=404,
                detail=(
                    "No active audit_rule found for rule_type "
                    f"'{request.rule_type}'"
                ),
            )

        return {
            "rule_type": request.rule_type,
            "rule_name": request.rule_name,
            "rule_id": request.rule_id,
            "source": source,
            "configs": [
                {
                    "config_id": row["config_id"],
                    "rule_id": row["rule_id"],
                    "rule_name": row["rule_name"],
                    "rule_type": row["rule_type"],
                    "rule_status": row["rule_status"],
                    "basis_hash": row["basis_hash"],
                    "status": row["status"],
                    "config": row["config"],
                }
                for row in configs
            ],
        }

    return router


def validate_request_config(
    config: dict[str, Any],
    mapping_config,
) -> dict[str, Any]:
    from .audit_graph_config import validate_graph_config

    return validate_graph_config(config, mapping_config)


def _merge_rule_configs(
    rule_configs: list[dict[str, Any]],
    request_max_depth: int | None,
) -> _TraversalConfig:
    relation_types: set[str] = set()
    allowed_entity_types: set[str] = set()
    default_depths: list[int] = []
    for row in rule_configs:
        config = row["config"]
        relation_types.update(config.get("relation_types", []))
        allowed_entity_types.update(config.get("allowed_entity_types", []))
        default_depths.append(int(config.get("default_max_depth", 4)))

    max_depth = request_max_depth or min(default_depths or [4])
    max_depth = max(1, min(max_depth, MAX_ALLOWED_DEPTH))
    return _TraversalConfig(
        relation_types=relation_types,
        allowed_entity_types=allowed_entity_types,
        max_depth=max_depth,
    )


async def _find_paths_optimized(
    graph,
    start_name: str,
    end_name: str,
    config: _TraversalConfig,
    limit: int,
) -> list[dict[str, Any]]:
    if _supports_neo4j_cypher_paths(graph):
        try:
            return await _find_paths_neo4j(
                graph,
                start_name,
                end_name,
                config,
                limit,
            )
        except Exception as exc:
            logger.warning(
                "Neo4j graph path fast path failed; falling back to BFS: %s",
                exc,
            )
    return await _find_paths(graph, start_name, end_name, config, limit)


def _supports_neo4j_cypher_paths(graph) -> bool:
    return (
        getattr(graph, "_driver", None) is not None
        and hasattr(graph, "_get_workspace_label")
        and hasattr(graph, "_DATABASE")
    )


async def _find_paths_neo4j(
    graph,
    start_name: str,
    end_name: str,
    config: _TraversalConfig,
    limit: int,
) -> list[dict[str, Any]]:
    workspace_label = graph._get_workspace_label()
    depth = config.max_depth
    query = f"""
    MATCH (start:`{workspace_label}` {{entity_id: $start_name}})
    MATCH (end:`{workspace_label}` {{entity_id: $end_name}})
    MATCH p = (start)-[:DIRECTED*1..{depth}]-(end)
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
    """
    async with graph._driver.session(
        database=graph._DATABASE,
        default_access_mode="READ",
    ) as session:
        result = await session.run(
            query,
            start_name=start_name,
            end_name=end_name,
            allowed_entity_types=sorted(config.allowed_entity_types),
            relation_types=sorted(config.relation_types),
            limit=limit,
        )
        try:
            paths = []
            async for record in result:
                paths.append(_neo4j_path_response(record))
            return paths
        finally:
            await result.consume()


def _neo4j_path_response(record: Any) -> dict[str, Any]:
    node_names = list(record["node_names"])
    nodes_data = list(record["nodes"])
    edges_data = list(record["edges"])
    edge_sources = list(record["edge_sources"])
    edge_targets = list(record["edge_targets"])

    nodes = [
        _node_response(str(node_name), dict(node_data or {}))
        for node_name, node_data in zip(node_names, nodes_data)
    ]
    edges = []
    for index, edge in enumerate(edges_data):
        edge_data = dict(edge or {})
        current = node_names[index]
        neighbor = node_names[index + 1]
        edges.append(
            {
                "from": str(current),
                "to": str(neighbor),
                "type": str(edge_data.get("keywords") or ""),
                "description": str(edge_data.get("description") or ""),
                "source_id": str(edge_data.get("source_id") or ""),
                "file_path": str(edge_data.get("file_path") or ""),
                "weight": edge_data.get("weight"),
            }
        )

    return {
        "depth": int(record["depth"]),
        "nodes": nodes,
        "edges": edges,
    }


async def _find_paths(
    graph,
    start_name: str,
    end_name: str,
    config: _TraversalConfig,
    limit: int,
) -> list[dict[str, Any]]:
    paths: list[dict[str, Any]] = []
    queue = deque([(start_name, [start_name], [])])

    while queue and len(paths) < limit:
        current, node_names, edge_steps = queue.popleft()
        depth = len(edge_steps)
        if current == end_name and depth > 0:
            paths.append(await _path_response(graph, node_names, edge_steps))
            continue
        if depth >= config.max_depth:
            continue

        edges = await graph.get_node_edges(current)
        if not edges:
            continue

        for source, target in edges:
            neighbor = target if source == current else source
            if neighbor in node_names:
                continue

            edge = await _get_edge_between(graph, source, target)
            if not edge:
                continue
            relation_type = str(edge.get("keywords") or "")
            if config.relation_types and relation_type not in config.relation_types:
                continue

            neighbor_node = await graph.get_node(neighbor)
            if neighbor_node is None:
                continue
            if not _node_type_allowed(neighbor_node, config.allowed_entity_types):
                continue

            queue.append(
                (
                    neighbor,
                    [*node_names, neighbor],
                    [
                        *edge_steps,
                        {
                            "from": current,
                            "to": neighbor,
                            "source": source,
                            "target": target,
                            "data": dict(edge),
                        },
                    ],
                )
            )

    return paths


async def _get_edge_between(
    graph,
    source: str,
    target: str,
) -> dict[str, Any] | None:
    edge = await graph.get_edge(source, target)
    if edge is not None:
        return edge
    return await graph.get_edge(target, source)


async def _path_response(graph, node_names: list[str], edge_steps: list[dict[str, Any]]):
    nodes = []
    for node_name in node_names:
        node = await graph.get_node(node_name)
        nodes.append(_node_response(node_name, node or {}))

    edges = []
    for step in edge_steps:
        edge = step["data"]
        edges.append(
            {
                "from": step["from"],
                "to": step["to"],
                "type": str(edge.get("keywords") or ""),
                "description": str(edge.get("description") or ""),
                "source_id": str(edge.get("source_id") or ""),
                "file_path": str(edge.get("file_path") or ""),
                "weight": edge.get("weight"),
            }
        )

    return {
        "depth": len(edge_steps),
        "nodes": nodes,
        "edges": edges,
    }


def _node_response(name: str, node: dict[str, Any]) -> dict[str, Any]:
    return {
        "name": name,
        "entity_id": str(node.get("entity_id") or name),
        "entity_type": str(node.get("entity_type") or "UNKNOWN"),
        "description": str(node.get("description") or ""),
        "source_id": str(node.get("source_id") or ""),
        "file_path": str(node.get("file_path") or ""),
    }


def _node_type_allowed(node: dict[str, Any], allowed_entity_types: set[str]) -> bool:
    if not allowed_entity_types:
        return True
    return str(node.get("entity_type") or "UNKNOWN") in allowed_entity_types
