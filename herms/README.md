# Herms Audit Relation MCP Tools

This directory keeps the original Dify workflow exports and a lightweight MCP
server that exposes the same three audit graph relation capabilities for Herms.
The MCP server calls LightRAG directly and does not depend on Dify runtime.

## Tools

| MCP tool | Inputs | LightRAG graph queries |
| --- | --- | --- |
| `analyze_person_relation` | `user1`, `user2`, optional `question` | `人际关系`, `max_depth=5`, `limit=50` |
| `analyze_company_relation` | `company1`, `company2`, optional `question` | `投标行为`, `max_depth=4`, `limit=30`; `股权关系`, `max_depth=4`, `limit=30` |
| `analyze_company_person_relation` | `company`, `user`, optional `question` | `股权关系`, `max_depth=4`, `limit=20`; `人际关系`, `max_depth=1`, `limit=10` |

## Start

Run from the LightRAG repository root:

```bash
python -m herms.audit_relation_mcp_server
```

Configuration is read from environment variables:

| Variable | Default | Meaning |
| --- | --- | --- |
| `LIGHTRAG_BASE_URL` | `http://172.16.1.203:9621/lightRag` | LightRAG API base URL. Include `/lightRag` when the server is deployed with that prefix. |
| `LIGHTRAG_API_KEY` | empty | Optional API key. The server adds `Authorization: Bearer <value>` unless the value already starts with `Bearer `. |
| `LIGHTRAG_TIMEOUT` | `30` | HTTP timeout in seconds. |

## Herms MCP Config Example

Use the repository root as the working directory so Python can import the local
`herms` package:

```json
{
  "mcpServers": {
    "audit-relation-tools": {
      "command": "python",
      "args": ["-m", "herms.audit_relation_mcp_server"],
      "cwd": "D:\\Company\\Code\\Source-Code\\LightRAG",
      "env": {
        "LIGHTRAG_BASE_URL": "http://172.16.1.203:9621/lightRag"
      }
    }
  }
}
```

## Output

Each tool returns JSON text with:

- `analysis_type`: the selected capability.
- `input`: normalized input names and original question.
- `summary`: query count, success count, error count, and total path count.
- `results`: one item per LightRAG path query, preserving the raw path-query response or a per-query error.
- `analysis_guidance`: instruction text for the calling agent to produce a concise evidence-based answer.

The tool only queries the graph. It does not delete or mutate LightRAG data.
