from __future__ import annotations

import pytest

from herms.audit_relation_tools import (
    LightRAGGraphClient,
    analyze_company_person_relation,
    analyze_company_relation,
    analyze_person_relation,
)


class RecordingTransport:
    def __init__(self, responses: list[dict] | None = None) -> None:
        self.requests: list[dict] = []
        self.responses = responses or []

    def post_json(
        self,
        url: str,
        payload: dict,
        headers: dict[str, str],
        timeout: float,
    ) -> dict:
        self.requests.append(
            {
                "url": url,
                "payload": payload,
                "headers": headers,
                "timeout": timeout,
            }
        )
        if self.responses:
            return self.responses.pop(0)
        return {
            "business_type": payload["business_type"],
            "paths": [{"depth": payload["max_depth"], "nodes": [], "edges": []}],
        }


def test_person_relation_uses_dify_path_query_defaults() -> None:
    transport = RecordingTransport()
    client = LightRAGGraphClient(
        base_url="http://lightrag.example/lightRag",
        api_key="secret",
        transport=transport,
    )

    result = analyze_person_relation(client, user1="张三", user2="李四")

    assert result["analysis_type"] == "person_relation"
    assert result["summary"]["total_paths"] == 1
    assert transport.requests == [
        {
            "url": "http://lightrag.example/lightRag/audit/graph/paths/query",
            "payload": {
                "start": {"name": "张三"},
                "end": {"name": "李四"},
                "business_type": "人际关系",
                "max_depth": 5,
                "limit": 50,
            },
            "headers": {"Authorization": "Bearer secret"},
            "timeout": 30.0,
        }
    ]


def test_company_relation_queries_bidding_and_equity_paths() -> None:
    transport = RecordingTransport()
    client = LightRAGGraphClient(base_url="http://lightrag.example", transport=transport)

    result = analyze_company_relation(
        client,
        company1="甲公司",
        company2="乙公司",
    )

    assert result["analysis_type"] == "company_relation"
    assert [item["business_type"] for item in result["results"]] == [
        "投标行为",
        "股权关系",
    ]
    assert [request["payload"] for request in transport.requests] == [
        {
            "start": {"name": "甲公司"},
            "end": {"name": "乙公司"},
            "business_type": "投标行为",
            "max_depth": 4,
            "limit": 30,
        },
        {
            "start": {"name": "甲公司"},
            "end": {"name": "乙公司"},
            "business_type": "股权关系",
            "max_depth": 4,
            "limit": 30,
        },
    ]


def test_company_person_relation_queries_equity_and_affiliation_paths() -> None:
    transport = RecordingTransport()
    client = LightRAGGraphClient(base_url="http://lightrag.example", transport=transport)

    result = analyze_company_person_relation(
        client,
        company="甲公司",
        user="张三",
    )

    assert result["analysis_type"] == "company_person_relation"
    assert [item["business_type"] for item in result["results"]] == [
        "股权关系",
        "人际关系",
    ]
    assert [request["payload"] for request in transport.requests] == [
        {
            "start": {"name": "甲公司"},
            "end": {"name": "张三"},
            "business_type": "股权关系",
            "max_depth": 4,
            "limit": 20,
        },
        {
            "start": {"name": "甲公司"},
            "end": {"name": "张三"},
            "business_type": "人际关系",
            "max_depth": 1,
            "limit": 10,
        },
    ]


def test_client_rejects_blank_names_before_http_call() -> None:
    transport = RecordingTransport()
    client = LightRAGGraphClient(base_url="http://lightrag.example", transport=transport)

    with pytest.raises(ValueError, match="start and end names must be non-empty"):
        client.query_paths(" ", "张三", "人际关系", max_depth=5, limit=50)

    assert transport.requests == []


def test_analysis_result_preserves_per_query_errors() -> None:
    class FailingTransport(RecordingTransport):
        def post_json(
            self,
            url: str,
            payload: dict,
            headers: dict[str, str],
            timeout: float,
        ) -> dict:
            self.requests.append({"url": url, "payload": payload})
            if payload["business_type"] == "投标行为":
                raise RuntimeError("service unavailable")
            return {"business_type": "股权关系", "paths": []}

    transport = FailingTransport()
    client = LightRAGGraphClient(base_url="http://lightrag.example", transport=transport)

    result = analyze_company_relation(client, company1="甲公司", company2="乙公司")

    assert result["summary"] == {
        "query_count": 2,
        "success_count": 1,
        "error_count": 1,
        "total_paths": 0,
    }
    assert result["results"][0]["ok"] is False
    assert result["results"][0]["error"] == "service unavailable"
    assert result["results"][1]["ok"] is True
