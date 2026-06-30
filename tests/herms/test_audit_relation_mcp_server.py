from __future__ import annotations

import json
import subprocess
import sys

from herms import audit_relation_mcp_server as server


class StubClient:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []


def test_tools_list_exposes_three_relation_analysis_tools() -> None:
    response = server.handle_jsonrpc(
        {"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
        client_factory=StubClient,
    )

    assert response["id"] == 1
    assert [tool["name"] for tool in response["result"]["tools"]] == [
        "analyze_person_relation",
        "analyze_company_relation",
        "analyze_company_person_relation",
    ]
    person_schema = response["result"]["tools"][0]["inputSchema"]
    assert person_schema["required"] == ["user1", "user2"]


def test_tools_call_dispatches_person_relation_tool(monkeypatch) -> None:
    client = StubClient()

    def fake_analyze_person_relation(client_arg, *, user1, user2, question=None):
        assert client_arg is client
        return {"analysis_type": "person_relation", "input": [user1, user2, question]}

    monkeypatch.setattr(
        server,
        "analyze_person_relation",
        fake_analyze_person_relation,
    )

    response = server.handle_jsonrpc(
        {
            "jsonrpc": "2.0",
            "id": 2,
            "method": "tools/call",
            "params": {
                "name": "analyze_person_relation",
                "arguments": {"user1": "张三", "user2": "李四", "question": "是否有关联"},
            },
        },
        client_factory=lambda: client,
    )

    content = response["result"]["content"]
    assert content[0]["type"] == "text"
    assert json.loads(content[0]["text"]) == {
        "analysis_type": "person_relation",
        "input": ["张三", "李四", "是否有关联"],
    }


def test_tools_call_reports_unknown_tool_as_jsonrpc_error() -> None:
    response = server.handle_jsonrpc(
        {
            "jsonrpc": "2.0",
            "id": 3,
            "method": "tools/call",
            "params": {"name": "missing_tool", "arguments": {}},
        },
        client_factory=StubClient,
    )

    assert response["id"] == 3
    assert response["error"]["code"] == -32602
    assert "Unknown tool" in response["error"]["message"]


def test_tools_call_validates_required_arguments() -> None:
    response = server.handle_jsonrpc(
        {
            "jsonrpc": "2.0",
            "id": 4,
            "method": "tools/call",
            "params": {"name": "analyze_company_relation", "arguments": {"company1": "甲"}},
        },
        client_factory=StubClient,
    )

    assert response["error"]["code"] == -32602
    assert "Missing required argument: company2" == response["error"]["message"]


def test_initialize_returns_server_capabilities() -> None:
    response = server.handle_jsonrpc(
        {"jsonrpc": "2.0", "id": 5, "method": "initialize", "params": {}},
        client_factory=StubClient,
    )

    assert response["result"]["serverInfo"]["name"] == "herms-audit-relation-tools"
    assert response["result"]["capabilities"] == {"tools": {}}


def test_stdio_server_responds_to_tools_list() -> None:
    request = json.dumps(
        {"jsonrpc": "2.0", "id": 6, "method": "tools/list"},
        ensure_ascii=False,
    )

    completed = subprocess.run(
        [sys.executable, "-m", "herms.audit_relation_mcp_server"],
        input=request + "\n",
        text=True,
        capture_output=True,
        timeout=10,
        check=True,
    )

    response = json.loads(completed.stdout.strip())
    assert response["id"] == 6
    assert response["result"]["tools"][0]["name"] == "analyze_person_relation"
