"""Herms-facing audit graph relation analysis helpers.

The helpers in this module intentionally mirror the three Dify workflows under
``herms/*.yml`` while keeping execution independent from Dify.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Any, Protocol
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


DEFAULT_LIGHTRAG_BASE_URL = "http://172.16.1.203:9621/lightRag"
GRAPH_PATH_QUERY_PATH = "/audit/graph/paths/query"


class JsonPostTransport(Protocol):
    def post_json(
        self,
        url: str,
        payload: dict[str, Any],
        headers: dict[str, str],
        timeout: float,
    ) -> dict[str, Any]:
        """Post JSON and return the decoded JSON response."""


class UrllibJsonPostTransport:
    """Small standard-library JSON transport for the MCP tools."""

    def post_json(
        self,
        url: str,
        payload: dict[str, Any],
        headers: dict[str, str],
        timeout: float,
    ) -> dict[str, Any]:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request = Request(
            url,
            data=body,
            method="POST",
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json",
                **headers,
            },
        )
        try:
            with urlopen(request, timeout=timeout) as response:
                raw = response.read().decode("utf-8")
        except HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"LightRAG HTTP {exc.code}: {detail}") from exc
        except URLError as exc:
            raise RuntimeError(f"LightRAG request failed: {exc.reason}") from exc

        if not raw.strip():
            return {}
        try:
            decoded = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise RuntimeError("LightRAG returned non-JSON response") from exc
        if not isinstance(decoded, dict):
            raise RuntimeError("LightRAG returned a non-object JSON response")
        return decoded


@dataclass
class LightRAGGraphClient:
    """Client for LightRAG's audit graph path-query endpoint."""

    base_url: str = DEFAULT_LIGHTRAG_BASE_URL
    api_key: str | None = None
    timeout: float = 30.0
    transport: JsonPostTransport = field(default_factory=UrllibJsonPostTransport)

    @classmethod
    def from_env(cls) -> "LightRAGGraphClient":
        timeout_text = os.getenv("LIGHTRAG_TIMEOUT", "30")
        try:
            timeout = float(timeout_text)
        except ValueError as exc:
            raise ValueError("LIGHTRAG_TIMEOUT must be a number") from exc

        return cls(
            base_url=os.getenv("LIGHTRAG_BASE_URL", DEFAULT_LIGHTRAG_BASE_URL),
            api_key=os.getenv("LIGHTRAG_API_KEY") or None,
            timeout=timeout,
        )

    @property
    def query_url(self) -> str:
        return f"{self.base_url.rstrip('/')}{GRAPH_PATH_QUERY_PATH}"

    def query_paths(
        self,
        start_name: str,
        end_name: str,
        business_type: str,
        *,
        max_depth: int,
        limit: int,
    ) -> dict[str, Any]:
        start = start_name.strip()
        end = end_name.strip()
        business = business_type.strip()
        if not start or not end:
            raise ValueError("start and end names must be non-empty")
        if not business:
            raise ValueError("business_type must be non-empty")

        payload = {
            "start": {"name": start},
            "end": {"name": end},
            "business_type": business,
            "max_depth": max_depth,
            "limit": limit,
        }
        return self.transport.post_json(
            self.query_url,
            payload,
            self._auth_headers(),
            self.timeout,
        )

    def _auth_headers(self) -> dict[str, str]:
        if not self.api_key:
            return {}
        token = self.api_key.strip()
        if token.lower().startswith("bearer "):
            return {"Authorization": token}
        return {"Authorization": f"Bearer {token}"}


@dataclass(frozen=True)
class PathQuerySpec:
    business_type: str
    max_depth: int
    limit: int
    purpose: str


PERSON_RELATION_SPECS = (
    PathQuerySpec("人际关系", 5, 50, "人员之间的人际、任职、项目和投标相关路径"),
)
COMPANY_RELATION_SPECS = (
    PathQuerySpec("投标行为", 4, 30, "公司之间的投标行为关联路径"),
    PathQuerySpec("股权关系", 4, 30, "公司之间的股权关系路径"),
)
COMPANY_PERSON_RELATION_SPECS = (
    PathQuerySpec("股权关系", 4, 20, "公司和人员之间的股权关系路径"),
    PathQuerySpec("人际关系", 1, 10, "公司和人员之间的一跳所属或任职关系路径"),
)


def analyze_person_relation(
    client: LightRAGGraphClient,
    *,
    user1: str,
    user2: str,
    question: str | None = None,
) -> dict[str, Any]:
    return _run_relation_analysis(
        client,
        analysis_type="person_relation",
        start=user1,
        end=user2,
        start_role="user1",
        end_role="user2",
        specs=PERSON_RELATION_SPECS,
        question=question,
    )


def analyze_company_relation(
    client: LightRAGGraphClient,
    *,
    company1: str,
    company2: str,
    question: str | None = None,
) -> dict[str, Any]:
    return _run_relation_analysis(
        client,
        analysis_type="company_relation",
        start=company1,
        end=company2,
        start_role="company1",
        end_role="company2",
        specs=COMPANY_RELATION_SPECS,
        question=question,
    )


def analyze_company_person_relation(
    client: LightRAGGraphClient,
    *,
    company: str,
    user: str,
    question: str | None = None,
) -> dict[str, Any]:
    return _run_relation_analysis(
        client,
        analysis_type="company_person_relation",
        start=company,
        end=user,
        start_role="company",
        end_role="user",
        specs=COMPANY_PERSON_RELATION_SPECS,
        question=question,
    )


def _run_relation_analysis(
    client: LightRAGGraphClient,
    *,
    analysis_type: str,
    start: str,
    end: str,
    start_role: str,
    end_role: str,
    specs: tuple[PathQuerySpec, ...],
    question: str | None,
) -> dict[str, Any]:
    results = []
    for spec in specs:
        try:
            data = client.query_paths(
                start,
                end,
                spec.business_type,
                max_depth=spec.max_depth,
                limit=spec.limit,
            )
        except Exception as exc:
            results.append(_error_result(spec, exc))
            continue
        results.append(_success_result(spec, data))

    return {
        "analysis_type": analysis_type,
        "input": {
            start_role: start.strip(),
            end_role: end.strip(),
            "question": question or "",
        },
        "summary": _summary(results),
        "results": results,
        "analysis_guidance": _analysis_guidance(analysis_type),
    }


def _success_result(spec: PathQuerySpec, data: dict[str, Any]) -> dict[str, Any]:
    paths = data.get("paths")
    path_count = len(paths) if isinstance(paths, list) else 0
    return {
        "ok": True,
        "business_type": spec.business_type,
        "purpose": spec.purpose,
        "max_depth": spec.max_depth,
        "limit": spec.limit,
        "path_count": path_count,
        "data": data,
    }


def _error_result(spec: PathQuerySpec, exc: Exception) -> dict[str, Any]:
    return {
        "ok": False,
        "business_type": spec.business_type,
        "purpose": spec.purpose,
        "max_depth": spec.max_depth,
        "limit": spec.limit,
        "path_count": 0,
        "error": str(exc),
        "data": None,
    }


def _summary(results: list[dict[str, Any]]) -> dict[str, int]:
    success_count = sum(1 for item in results if item["ok"])
    return {
        "query_count": len(results),
        "success_count": success_count,
        "error_count": len(results) - success_count,
        "total_paths": sum(int(item.get("path_count") or 0) for item in results),
    }


def _analysis_guidance(analysis_type: str) -> str:
    guidance = {
        "person_relation": (
            "基于 results 中的人际关系路径，说明两名人员是否存在任职、项目、投标或股权相关关联。"
        ),
        "company_relation": (
            "基于 results 中的投标行为和股权关系路径，说明两家公司是否存在陪标或股权关联证据。"
        ),
        "company_person_relation": (
            "基于 results 中的股权关系和一跳人际关系路径，说明公司与人员是否存在股权或所属任职关系。"
        ),
    }
    return (
        guidance[analysis_type]
        + " 结论应贴近返回路径证据；没有路径时说明未查询到对应关系，不要主观臆测。"
    )
