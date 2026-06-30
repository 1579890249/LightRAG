"""Stdio MCP server exposing Herms audit relation analysis tools."""

from __future__ import annotations

import json
import sys
from collections.abc import Callable
from typing import Any

from .audit_relation_tools import (
    LightRAGGraphClient,
    analyze_company_person_relation,
    analyze_company_relation,
    analyze_person_relation,
)


SERVER_NAME = "herms-audit-relation-tools"
SERVER_VERSION = "0.1.0"


def _text_schema(description: str) -> dict[str, Any]:
    return {"type": "string", "description": description, "minLength": 1}


TOOLS: list[dict[str, Any]] = [
    {
        "name": "analyze_person_relation",
        "description": "分析两名人员之间的人际、任职、项目、投标或股权相关路径。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "user1": _text_schema("第一名人员名称"),
                "user2": _text_schema("第二名人员名称"),
                "question": {"type": "string", "description": "用户原始问题"},
            },
            "required": ["user1", "user2"],
            "additionalProperties": False,
        },
    },
    {
        "name": "analyze_company_relation",
        "description": "分析两家公司之间的投标行为和股权关系路径。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "company1": _text_schema("第一家公司名称"),
                "company2": _text_schema("第二家公司名称"),
                "question": {"type": "string", "description": "用户原始问题"},
            },
            "required": ["company1", "company2"],
            "additionalProperties": False,
        },
    },
    {
        "name": "analyze_company_person_relation",
        "description": "分析公司和人员之间的股权关系以及一跳所属或任职关系。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "company": _text_schema("公司名称"),
                "user": _text_schema("人员名称"),
                "question": {"type": "string", "description": "用户原始问题"},
            },
            "required": ["company", "user"],
            "additionalProperties": False,
        },
    },
]


def handle_jsonrpc(
    message: dict[str, Any],
    *,
    client_factory: Callable[[], LightRAGGraphClient] = LightRAGGraphClient.from_env,
) -> dict[str, Any] | None:
    """Handle one JSON-RPC message from an MCP client."""

    request_id = message.get("id")
    method = message.get("method")
    try:
        if method == "initialize":
            return _response(request_id, _initialize_result())
        if method == "notifications/initialized":
            return None
        if method == "tools/list":
            return _response(request_id, {"tools": TOOLS})
        if method == "tools/call":
            return _response(request_id, _call_tool(message.get("params"), client_factory))
        raise JsonRpcError(-32601, f"Method not found: {method}")
    except JsonRpcError as exc:
        return _error_response(request_id, exc.code, exc.message)
    except Exception as exc:
        return _error_response(request_id, -32603, str(exc))


def _initialize_result() -> dict[str, Any]:
    return {
        "protocolVersion": "2024-11-05",
        "capabilities": {"tools": {}},
        "serverInfo": {
            "name": SERVER_NAME,
            "version": SERVER_VERSION,
        },
    }


def _call_tool(
    params: Any,
    client_factory: Callable[[], LightRAGGraphClient],
) -> dict[str, Any]:
    if not isinstance(params, dict):
        raise JsonRpcError(-32602, "tools/call params must be an object")
    tool_name = params.get("name")
    arguments = params.get("arguments") or {}
    if not isinstance(arguments, dict):
        raise JsonRpcError(-32602, "tools/call arguments must be an object")

    client = client_factory()
    if tool_name == "analyze_person_relation":
        _require_arguments(arguments, "user1", "user2")
        result = analyze_person_relation(
            client,
            user1=str(arguments["user1"]),
            user2=str(arguments["user2"]),
            question=_optional_text(arguments.get("question")),
        )
    elif tool_name == "analyze_company_relation":
        _require_arguments(arguments, "company1", "company2")
        result = analyze_company_relation(
            client,
            company1=str(arguments["company1"]),
            company2=str(arguments["company2"]),
            question=_optional_text(arguments.get("question")),
        )
    elif tool_name == "analyze_company_person_relation":
        _require_arguments(arguments, "company", "user")
        result = analyze_company_person_relation(
            client,
            company=str(arguments["company"]),
            user=str(arguments["user"]),
            question=_optional_text(arguments.get("question")),
        )
    else:
        raise JsonRpcError(-32602, f"Unknown tool: {tool_name}")

    return {
        "content": [
            {
                "type": "text",
                "text": json.dumps(result, ensure_ascii=False, indent=2),
            }
        ],
        "isError": False,
    }


def _require_arguments(arguments: dict[str, Any], *names: str) -> None:
    for name in names:
        value = arguments.get(name)
        if not isinstance(value, str) or not value.strip():
            raise JsonRpcError(-32602, f"Missing required argument: {name}")


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    return str(value)


def _response(request_id: Any, result: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def _error_response(request_id: Any, code: int, message: str) -> dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "error": {"code": code, "message": message},
    }


class JsonRpcError(Exception):
    def __init__(self, code: int, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def main() -> int:
    for line in sys.stdin:
        if not line.strip():
            continue
        try:
            message = json.loads(line)
            if not isinstance(message, dict):
                raise JsonRpcError(-32600, "JSON-RPC message must be an object")
            response = handle_jsonrpc(message)
        except json.JSONDecodeError as exc:
            response = _error_response(None, -32700, f"Parse error: {exc.msg}")
        except JsonRpcError as exc:
            response = _error_response(None, exc.code, exc.message)

        if response is not None:
            sys.stdout.write(json.dumps(response, ensure_ascii=False) + "\n")
            sys.stdout.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
