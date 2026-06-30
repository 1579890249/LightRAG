import sqlite3
from types import SimpleNamespace

import pytest
from fastapi import FastAPI, Header, HTTPException
from fastapi.testclient import TestClient

from lightrag.api.routers.audit_routes import create_audit_routes


pytestmark = pytest.mark.offline

_API_KEY = "test-key"
_HEADERS = {"X-API-Key": _API_KEY}


class FakeGraphStorage:
    def __init__(self):
        self.nodes = {
            "张三": {
                "entity_id": "张三",
                "entity_type": "Person",
                "description": "bid contact",
            },
            "李四": {
                "entity_id": "李四",
                "entity_type": "Person",
                "description": "intermediate person",
            },
            "华信科技": {
                "entity_id": "华信科技",
                "entity_type": "Organization",
                "description": "bidder company",
            },
            "无关项目": {
                "entity_id": "无关项目",
                "entity_type": "Project",
                "description": "unrelated project",
            },
        }
        self.edges = {
            ("张三", "李四"): {
                "keywords": "PERSON_RELATED",
                "description": "张三与李四存在同事关系",
                "source_id": "db://audit/person_relation/1",
            },
            ("李四", "华信科技"): {
                "keywords": "HOLDS_POSITION",
                "description": "李四在华信科技任职",
                "source_id": "db://audit/person_enterprise_position/1",
            },
            ("张三", "无关项目"): {
                "keywords": "FOR_PROJECT",
                "description": "This relation should be filtered out",
                "source_id": "db://audit/bid_record/B1",
            },
        }

    async def get_node(self, node_id):
        return self.nodes.get(node_id)

    async def get_node_edges(self, node_id):
        if node_id not in self.nodes:
            return None
        result = []
        for source, target in self.edges:
            if source == node_id:
                result.append((source, target))
            elif target == node_id:
                result.append((target, source))
        return result

    async def get_edge(self, source, target):
        return self.edges.get((source, target)) or self.edges.get((target, source))


class FakeEquityGraphStorage(FakeGraphStorage):
    def __init__(self):
        self.nodes = {
            "张伟": {
                "entity_id": "张伟",
                "entity_type": "Person",
                "description": "natural person shareholder",
            },
            "张伟持有深圳华信科技有限公司42.50%股权（1）": {
                "entity_id": "张伟持有深圳华信科技有限公司42.50%股权（1）",
                "entity_type": "ShareholdingRecord",
                "description": "张伟持有深圳华信科技有限公司42.50%股权",
            },
            "深圳华信科技有限公司": {
                "entity_id": "深圳华信科技有限公司",
                "entity_type": "Organization",
                "description": "bidder company",
            },
            "深圳华信科技有限公司投标广新智慧采购监管平台（B101）": {
                "entity_id": "深圳华信科技有限公司投标广新智慧采购监管平台（B101）",
                "entity_type": "BidSubmission",
                "description": "bid submission",
            },
            "广新智慧采购监管平台": {
                "entity_id": "广新智慧采购监管平台",
                "entity_type": "Project",
                "description": "project",
            },
            "广新控股集团": {
                "entity_id": "广新控股集团",
                "entity_type": "Organization",
                "description": "tender organization",
            },
            "张伟持有广新控股集团12.00%股权（2）": {
                "entity_id": "张伟持有广新控股集团12.00%股权（2）",
                "entity_type": "ShareholdingRecord",
                "description": "张伟持有广新控股集团12.00%股权",
            },
        }
        self.edges = {
            (
                "张伟持有深圳华信科技有限公司42.50%股权（1）",
                "张伟",
            ): {
                "keywords": "NATURAL_PERSON_SHAREHOLDER",
                "description": "张伟 is a natural person shareholder of 深圳华信科技有限公司.",
                "source_id": "db://audit/enterprise_shareholding/1",
            },
            (
                "张伟持有深圳华信科技有限公司42.50%股权（1）",
                "深圳华信科技有限公司",
            ): {
                "keywords": "SHAREHOLDING_TARGET",
                "description": "张伟 holds 42.50% of 深圳华信科技有限公司.",
                "source_id": "db://audit/enterprise_shareholding/1",
            },
            (
                "深圳华信科技有限公司投标广新智慧采购监管平台（B101）",
                "深圳华信科技有限公司",
            ): {
                "keywords": "BIDDER",
                "description": "深圳华信科技有限公司 submitted bid B101.",
                "source_id": "db://audit/bid_record/B101",
            },
            (
                "深圳华信科技有限公司投标广新智慧采购监管平台（B101）",
                "广新智慧采购监管平台",
            ): {
                "keywords": "FOR_PROJECT",
                "description": "B101 belongs to 广新智慧采购监管平台.",
                "source_id": "db://audit/bid_record/B101",
            },
            ("广新智慧采购监管平台", "广新控股集团"): {
                "keywords": "TENDERED_BY",
                "description": "广新智慧采购监管平台 is tendered by 广新控股集团.",
                "source_id": "db://audit/project/PJT101",
            },
            ("张伟持有广新控股集团12.00%股权（2）", "张伟"): {
                "keywords": "NATURAL_PERSON_SHAREHOLDER",
                "description": "张伟 is a natural person shareholder of 广新控股集团.",
                "source_id": "db://audit/enterprise_shareholding/2",
            },
            ("张伟持有广新控股集团12.00%股权（2）", "广新控股集团"): {
                "keywords": "SHAREHOLDING_TARGET",
                "description": "张伟 holds 12.00% of 广新控股集团.",
                "source_id": "db://audit/enterprise_shareholding/2",
            },
        }


class DirectionalEdgeLookupGraphStorage(FakeGraphStorage):
    async def get_edge(self, source, target):
        return self.edges.get((source, target))


class FakeNeo4jResult:
    def __init__(self, records):
        self._records = records

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return None

    def __aiter__(self):
        return self

    async def __anext__(self):
        if not self._records:
            raise StopAsyncIteration
        return self._records.pop(0)

    async def consume(self):
        return None


class FakeNeo4jSession:
    def __init__(self, graph):
        self.graph = graph

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return None

    async def run(self, query, **params):
        self.graph.cypher_query = query
        self.graph.cypher_params = params
        record = {
            "node_names": ["张三", "李四", "华信科技"],
            "nodes": [
                self.graph.nodes["张三"],
                self.graph.nodes["李四"],
                self.graph.nodes["华信科技"],
            ],
            "edges": [
                self.graph.edges[("张三", "李四")],
                self.graph.edges[("李四", "华信科技")],
            ],
            "edge_sources": ["张三", "李四"],
            "edge_targets": ["李四", "华信科技"],
            "depth": 2,
        }
        return FakeNeo4jResult([record])


class FakeNeo4jDriver:
    def __init__(self, graph):
        self.graph = graph

    def session(self, database=None, default_access_mode=None):
        self.graph.session_database = database
        self.graph.session_access_mode = default_access_mode
        return FakeNeo4jSession(self.graph)


class FakeNeo4jGraphStorage(FakeGraphStorage):
    _DATABASE = "neo4j"

    def __init__(self):
        super().__init__()
        self._driver = FakeNeo4jDriver(self)
        self.cypher_query = None
        self.cypher_params = None

    def _get_workspace_label(self):
        return "audit_customer_ys"

    async def get_node_edges(self, node_id):
        raise AssertionError("Neo4j fast path should not call BFS edge expansion")


def _write_mapping(path):
    path.write_text(
        """
schema_version: audit_kg_v1
database_name: audit
sources:
  person:
    primary_key: person_id
  enterprise:
    primary_key: enterprise_id
  person_relation:
    primary_key: id
  person_enterprise_position:
    primary_key: id
  bid_record:
    primary_key: bid_id
  bid_person_role:
    primary_key: id
  project:
    primary_key: project_id
  enterprise_shareholding:
    primary_key: id
entity_types:
  Person:
    id_prefix: Person
  Organization:
    id_prefix: Organization
  Project:
    id_prefix: Project
  BidSubmission:
    id_prefix: BidSubmission
  ShareholdingRecord:
    id_prefix: ShareholdingRecord
entities:
  - source: person
    entity_type: Person
    id_field: person_id
  - source: enterprise
    entity_type: Organization
    id_field: enterprise_id
  - source: bid_record
    entity_type: Project
    id_field: project_id
  - source: bid_record
    entity_type: BidSubmission
    id_field: bid_id
  - source: enterprise_shareholding
    entity_type: ShareholdingRecord
    id_field: id
relationships:
  - source: person_relation
    relation_type: PERSON_RELATED
    src:
      entity_type: Person
      id_field: person_id_1
    tgt:
      entity_type: Person
      id_field: person_id_2
  - source: person_enterprise_position
    relation_type: HOLDS_POSITION
    src:
      entity_type: Person
      id_field: person_id
    tgt:
      entity_type: Organization
      id_field: enterprise_id
  - source: bid_record
    relation_type: FOR_PROJECT
    src:
      entity_type: BidSubmission
      id_field: bid_id
    tgt:
      entity_type: Project
      id_field: project_id
  - source: bid_record
    relation_type: BIDDER
    src:
      entity_type: BidSubmission
      id_field: bid_id
    tgt:
      entity_type: Organization
      id_field: enterprise_id
  - source: project
    relation_type: TENDERED_BY
    src:
      entity_type: Project
      id_field: project_id
    tgt:
      entity_type: Organization
      id_field: tender_org_id
  - source: bid_person_role
    relation_type: BID_PERSON_ROLE
    src:
      entity_type: BidSubmission
      id_field: bid_id
    tgt:
      entity_type: Person
      id_field: person_id
  - source: bid_person_role
    relation_type: BID_ROLE_ORG
    src:
      entity_type: Person
      id_field: person_id
    tgt:
      entity_type: Organization
      id_field: enterprise_id
  - source: enterprise_shareholding
    relation_type: SHAREHOLDING_TARGET
    src:
      entity_type: ShareholdingRecord
      id_field: id
    tgt:
      entity_type: Organization
      id_field: target_enterprise_id
  - source: enterprise_shareholding
    relation_type: NATURAL_PERSON_SHAREHOLDER
    src:
      entity_type: ShareholdingRecord
      id_field: id
    tgt:
      entity_type: Person
      id_field: holder_person_id
  - source: enterprise_shareholding
    relation_type: ENTERPRISE_SHAREHOLDER
    src:
      entity_type: ShareholdingRecord
      id_field: id
    tgt:
      entity_type: Organization
      id_field: holder_enterprise_id
""",
        encoding="utf-8",
    )


def _build_db(
    path,
    with_config=True,
    rule_name="Relationship penetration",
    rule_type="招投标人际关系风险",
):
    with sqlite3.connect(path) as conn:
        conn.executescript(
            """
            CREATE TABLE audit_rule (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                rule_name TEXT,
                rule_basis TEXT,
                rule_status TEXT,
                rule_type TEXT,
                remark TEXT
            );
            INSERT INTO audit_rule (
                id, rule_name, rule_basis, rule_status, rule_type, remark
            ) VALUES (
                1,
                '{rule_name}',
                'Check personnel and company related paths.',
                '1',
                '{rule_type}',
                ''
            );
            CREATE TABLE audit_rule_graph_config (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                rule_id INTEGER NOT NULL,
                config TEXT NOT NULL,
                basis_hash TEXT,
                status TEXT NOT NULL DEFAULT 'active',
                created_at TEXT,
                updated_at TEXT
            );
            """
            .format(rule_name=rule_name, rule_type=rule_type)
        )
        if with_config:
            if rule_type == "股权关系":
                config = (
                    '{"allowed_entity_types":'
                    '["Person","Organization","Project","BidSubmission",'
                    '"ShareholdingRecord"],'
                    '"relation_types":["BIDDER","FOR_PROJECT","TENDERED_BY",'
                    '"BID_PERSON_ROLE","BID_ROLE_ORG","SHAREHOLDING_TARGET",'
                    '"NATURAL_PERSON_SHAREHOLDER","ENTERPRISE_SHAREHOLDER"],'
                    '"default_max_depth":4}'
                )
            else:
                config = (
                    '{"allowed_entity_types":["Person","Organization"],'
                    '"relation_types":["PERSON_RELATED","HOLDS_POSITION"],'
                    '"default_max_depth":4}'
                )
            conn.execute(
                """
                INSERT INTO audit_rule_graph_config (
                    rule_id, config, basis_hash, status
                ) VALUES (
                    1,
                    ?,
                    'hash',
                    'active'
                )
                """,
                (config,),
            )
        conn.commit()


def _build_client(
    tmp_path,
    with_config=True,
    graph=None,
    rule_name="Relationship penetration",
    rule_type="招投标人际关系风险",
):
    async def _auth(x_api_key: str | None = Header(default=None)):
        if x_api_key != _API_KEY:
            raise HTTPException(status_code=403, detail="Invalid API Key")

    db_path = tmp_path / "audit.db"
    mapping_path = tmp_path / "mapping.yaml"
    _build_db(
        db_path,
        with_config=with_config,
        rule_name=rule_name,
        rule_type=rule_type,
    )
    _write_mapping(mapping_path)

    rag = SimpleNamespace(
        workspace="audit_customer_ys",
        chunk_entity_relation_graph=graph or FakeGraphStorage(),
    )
    app = FastAPI()
    app.include_router(
        create_audit_routes(
            rag,
            auth_dependency=_auth,
            default_audit_db_url=f"sqlite:///{db_path}",
            default_mapping_path=str(mapping_path),
        )
    )
    return TestClient(app)


def test_audit_graph_paths_query_uses_rule_type_config_to_return_paths(tmp_path):
    client = _build_client(tmp_path)

    response = client.post(
        "/audit/graph/paths/query",
        headers=_HEADERS,
        json={
            "start": {"name": "张三"},
            "end": {"name": "华信科技"},
            "business_type": "招投标人际关系风险",
            "max_depth": 4,
            "limit": 10,
        },
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["business_type"] == "招投标人际关系风险"
    assert body["start"]["name"] == "张三"
    assert body["end"]["name"] == "华信科技"
    assert body["rules"][0]["rule_id"] == 1
    assert len(body["paths"]) == 1
    assert body["paths"][0]["depth"] == 2
    assert [node["name"] for node in body["paths"][0]["nodes"]] == [
        "张三",
        "李四",
        "华信科技",
    ]
    assert [edge["type"] for edge in body["paths"][0]["edges"]] == [
        "PERSON_RELATED",
        "HOLDS_POSITION",
    ]
    assert "FOR_PROJECT" not in {
        edge["type"]
        for path in body["paths"]
        for edge in path["edges"]
    }


def test_audit_graph_paths_query_penetrates_natural_person_equity_to_bid_project(
    tmp_path,
):
    client = _build_client(
        tmp_path,
        graph=FakeEquityGraphStorage(),
        rule_name="股权交叉控股校验",
        rule_type="股权关系",
    )

    response = client.post(
        "/audit/graph/paths/query",
        headers=_HEADERS,
        json={
            "start": {"name": "张伟"},
            "end": {"name": "广新智慧采购监管平台"},
            "business_type": "股权关系",
            "max_depth": 4,
            "limit": 10,
        },
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["business_type"] == "股权关系"
    node_paths = [
        [node["name"] for node in path["nodes"]]
        for path in body["paths"]
    ]
    edge_type_paths = [
        [edge["type"] for edge in path["edges"]]
        for path in body["paths"]
    ]
    assert [
        "张伟",
        "张伟持有广新控股集团12.00%股权（2）",
        "广新控股集团",
        "广新智慧采购监管平台",
    ] in node_paths
    assert [
        "张伟",
        "张伟持有深圳华信科技有限公司42.50%股权（1）",
        "深圳华信科技有限公司",
        "深圳华信科技有限公司投标广新智慧采购监管平台（B101）",
        "广新智慧采购监管平台",
    ] in node_paths
    assert [
        "NATURAL_PERSON_SHAREHOLDER",
        "SHAREHOLDING_TARGET",
        "TENDERED_BY",
    ] in edge_type_paths
    assert [
        "NATURAL_PERSON_SHAREHOLDER",
        "SHAREHOLDING_TARGET",
        "BIDDER",
        "FOR_PROJECT",
    ] in edge_type_paths


def test_audit_graph_paths_query_penetrates_shareholder_to_tender_org(tmp_path):
    client = _build_client(
        tmp_path,
        graph=FakeEquityGraphStorage(),
        rule_name="股权交叉控股校验",
        rule_type="股权关系",
    )

    response = client.post(
        "/audit/graph/paths/query",
        headers=_HEADERS,
        json={
            "start": {"name": "张伟"},
            "end": {"name": "广新控股集团"},
            "business_type": "股权关系",
            "max_depth": 2,
            "limit": 10,
        },
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert len(body["paths"]) == 1
    assert [node["name"] for node in body["paths"][0]["nodes"]] == [
        "张伟",
        "张伟持有广新控股集团12.00%股权（2）",
        "广新控股集团",
    ]
    assert [edge["type"] for edge in body["paths"][0]["edges"]] == [
        "NATURAL_PERSON_SHAREHOLDER",
        "SHAREHOLDING_TARGET",
    ]


def test_audit_graph_paths_query_finds_companies_with_common_natural_shareholder(
    tmp_path,
):
    client = _build_client(
        tmp_path,
        graph=FakeEquityGraphStorage(),
        rule_name="股权交叉控股校验",
        rule_type="股权关系",
    )

    response = client.post(
        "/audit/graph/paths/query",
        headers=_HEADERS,
        json={
            "start": {"name": "深圳华信科技有限公司"},
            "end": {"name": "广新控股集团"},
            "business_type": "股权关系",
            "max_depth": 4,
            "limit": 10,
        },
    )

    assert response.status_code == 200, response.text
    body = response.json()
    node_paths = [
        [node["name"] for node in path["nodes"]]
        for path in body["paths"]
    ]
    assert [
        "深圳华信科技有限公司",
        "张伟持有深圳华信科技有限公司42.50%股权（1）",
        "张伟",
        "张伟持有广新控股集团12.00%股权（2）",
        "广新控股集团",
    ] in node_paths


def test_audit_graph_paths_query_returns_clear_error_without_compiled_config(tmp_path):
    client = _build_client(tmp_path, with_config=False)

    response = client.post(
        "/audit/graph/paths/query",
        headers=_HEADERS,
        json={
            "start": {"name": "张三"},
            "end": {"name": "华信科技"},
            "business_type": "招投标人际关系风险",
        },
    )

    assert response.status_code == 404
    assert "No active graph path config" in response.json()["detail"]


def test_audit_graph_paths_query_reads_reverse_edge_when_storage_requires_direction(
    tmp_path,
):
    client = _build_client(tmp_path, graph=DirectionalEdgeLookupGraphStorage())

    response = client.post(
        "/audit/graph/paths/query",
        headers=_HEADERS,
        json={
            "start": {"name": "华信科技"},
            "end": {"name": "张三"},
            "business_type": "招投标人际关系风险",
            "max_depth": 4,
            "limit": 10,
        },
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert len(body["paths"]) == 1
    assert [node["name"] for node in body["paths"][0]["nodes"]] == [
        "华信科技",
        "李四",
        "张三",
    ]
    assert [edge["type"] for edge in body["paths"][0]["edges"]] == [
        "HOLDS_POSITION",
        "PERSON_RELATED",
    ]


def test_audit_graph_paths_query_uses_neo4j_cypher_fast_path(tmp_path):
    graph = FakeNeo4jGraphStorage()
    client = _build_client(tmp_path, graph=graph)

    response = client.post(
        "/audit/graph/paths/query",
        headers=_HEADERS,
        json={
            "start": {"name": "张三"},
            "end": {"name": "华信科技"},
            "business_type": "招投标人际关系风险",
            "max_depth": 4,
            "limit": 10,
        },
    )

    assert response.status_code == 200, response.text
    body = response.json()
    assert len(body["paths"]) == 1
    assert body["paths"][0]["depth"] == 2
    assert [edge["type"] for edge in body["paths"][0]["edges"]] == [
        "PERSON_RELATED",
        "HOLDS_POSITION",
    ]
    assert "MATCH p = (start)-[:DIRECTED*1..4]-(end)" in graph.cypher_query
    assert graph.cypher_params["start_name"] == "张三"
    assert graph.cypher_params["end_name"] == "华信科技"
    assert graph.cypher_params["limit"] == 10


def test_audit_graph_rule_config_upsert_builds_type_template_without_explicit_config(
    tmp_path,
):
    client = _build_client(
        tmp_path,
        with_config=False,
        rule_name="人员关联关系校验",
        rule_type="人际关系",
    )

    upsert_response = client.post(
        "/audit/graph/rule-configs/upsert",
        headers=_HEADERS,
        json={
            "rule_type": "人际关系",
            "rule_name": "人员关联关系校验",
            "default_max_depth": 3,
        },
    )

    assert upsert_response.status_code == 200, upsert_response.text
    upsert_body = upsert_response.json()
    assert upsert_body["rule_type"] == "人际关系"
    assert upsert_body["source"] == "builtin_template"
    assert len(upsert_body["configs"]) == 1
    config = upsert_body["configs"][0]["config"]
    assert config["default_max_depth"] == 3
    assert "Person" in config["allowed_entity_types"]
    assert "Organization" in config["allowed_entity_types"]
    assert "PERSON_RELATED" in config["relation_types"]
    assert "HOLDS_POSITION" in config["relation_types"]

    query_response = client.post(
        "/audit/graph/paths/query",
        headers=_HEADERS,
        json={
            "start": {"name": "张三"},
            "end": {"name": "华信科技"},
            "business_type": "人际关系",
            "limit": 10,
        },
    )

    assert query_response.status_code == 200, query_response.text
    assert len(query_response.json()["paths"]) == 1


def test_audit_graph_rule_config_upsert_rejects_unknown_explicit_relation_type(
    tmp_path,
):
    client = _build_client(
        tmp_path,
        with_config=False,
        rule_name="人员关联关系校验",
        rule_type="人际关系",
    )

    response = client.post(
        "/audit/graph/rule-configs/upsert",
        headers=_HEADERS,
        json={
            "rule_type": "人际关系",
            "rule_name": "人员关联关系校验",
            "config": {
                "allowed_entity_types": ["Person"],
                "relation_types": ["UNKNOWN_RELATION"],
                "default_max_depth": 3,
            },
        },
    )

    assert response.status_code == 400
    assert "UNKNOWN_RELATION" in response.json()["detail"]
