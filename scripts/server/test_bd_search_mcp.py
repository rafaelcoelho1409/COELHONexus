"""Offline contract checks for BD Search MCP and source retrieval statuses.

    cd apps/server && .venv/bin/python ../../scripts/server/test_bd_search_mcp.py
"""
from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "apps" / "server"))
for name, value in {
    "REDIS_HOST": "localhost",
    "REDIS_PORT": "6379",
    "REDIS_PASSWORD": "x",
    "ENVIRONMENT": "test",
}.items():
    os.environ.setdefault(name, value)

import domains  # noqa: E402
import ai  # noqa: E402
from entrypoints.api.middleware import mcp as api_mcp_middleware  # noqa: E402
from entrypoints.mcp.components import bd as mcp_bd  # noqa: E402
from entrypoints.mcp.deployment import params as mcp_params  # noqa: E402
from entrypoints.mcp.deployment import service as mcp_server_service  # noqa: E402
from entrypoints.mcp.authentication import params as auth_params, service as auth_service  # noqa: E402
from fastmcp import Client, FastMCP  # noqa: E402
from fastmcp.server.auth.providers.jwt import JWTVerifier  # noqa: E402
from langchain_openai import ChatOpenAI  # noqa: E402


def check_scope_domain() -> None:
    domain = mcp_bd.tools.search.domain
    assert domain.safe_segment("book-01")
    assert not domain.safe_segment("../book-01")
    assert domain.book_is_allowed("book-01", frozenset({"book-01"}))
    assert not domain.book_is_allowed("book-02", frozenset({"book-01"}))
    assert domain.token_book_ids({"bd_book_ids": ["book-01", "../bad", 3]}) == {"book-01"}
    assert domain.normalize_query("  exact   phrase ", 100) == "exact phrase"


def check_ask_book_retrieval_subgraph_compiles() -> None:
    graph = ai.bd.graphs.ask.adaptive.subgraphs.book_retrieval.graphs.book_retrieval_graph()
    node_names = set(graph.get_graph().nodes)
    assert {"elasticsearch", "qdrant", "neo4j", "fuse_evidence"} <= node_names


def check_specialist_agents_build() -> None:
    model = ChatOpenAI(model = "gpt-4o-mini", api_key = "offline-test")
    builders = (
        ai.bd.agents.ask.elasticsearch_retriever.agent.elasticsearch_retriever,
        ai.bd.agents.ask.qdrant_retriever.agent.qdrant_retriever,
        ai.bd.agents.ask.neo4j_retriever.agent.neo4j_retriever,
        ai.bd.agents.distill.elasticsearch_retriever.agent.elasticsearch_retriever,
        ai.bd.agents.distill.qdrant_retriever.agent.qdrant_retriever,
        ai.bd.agents.distill.neo4j_retriever.agent.neo4j_retriever,
        ai.bd.agents.distill.web_search_retriever.agent.web_search_retriever,
    )
    agents = [builder(model) for builder in builders]
    assert [agent.name for agent in agents] == [
        "bd_ask_elasticsearch_retriever",
        "bd_ask_qdrant_retriever",
        "bd_ask_neo4j_retriever",
        "bd_distill_elasticsearch_retriever",
        "bd_distill_qdrant_retriever",
        "bd_distill_neo4j_retriever",
        "bd_distill_web_search_retriever",
    ]


def check_graph_fusion_preserves_sources() -> None:
    fused = ai.bd.nodes.ask.adaptive.subgraphs.book_retrieval.fuse.service.fuse_evidence({
        "queries": ["exact term"],
        "mode": "deep",
        "elasticsearch_results": [{
            "query": "exact term",
            "status": "complete",
            "evidence": [{
                "id": "es:1", "text": "same passage", "score": 1.0,
                "source": "elasticsearch_bm25", "citation": {"page": 1},
            }],
        }],
        "qdrant_results": [{
            "query": "exact term",
            "status": "complete",
            "evidence": [{
                "id": "qdrant:1", "text": "same passage", "score": 0.8,
                "source": "qdrant_hybrid", "citation": {"page": 1},
            }],
        }],
        "neo4j_results": [{
            "query": "exact term", "status": "timeout", "evidence": [],
        }],
    })
    assert fused["retrieval_source_status"] == {
        "exact term": {
            "elasticsearch": "complete", "qdrant": "complete", "neo4j": "timeout",
        },
    }
    assert len(fused["documents"]) == 1
    assert fused["documents"][0]["matched_sources"] == ["elasticsearch_bm25", "qdrant_hybrid"]


async def check_mcp_tool_contract() -> None:
    original = domains.bd.rag.ai.retrieval.service.retrieve_source

    async def fake_retrieve_source(book_id, run_id, query, source, *, top_k):
        assert (book_id, run_id, top_k) == ("book-01", "run-01", 8)
        return {
            "status": "complete",
            "source": source,
            "query": query,
            "evidence": [{
                "id": f"{source}:1",
                "text": "Ada Lovelace evidence",
                "score": 1.0,
                "source": f"{source}_test",
                "citation": {"book_id": book_id, "page": 1},
            }],
        }

    domains.bd.rag.ai.retrieval.service.retrieve_source = fake_retrieve_source
    server = FastMCP("BD Search test")
    mcp_bd.registry.register_tools(server, trusted_in_process = True)
    try:
        async with Client(server) as client:
            tools = await client.list_tools()
            names = {item.name for item in tools}
            assert names == {
                "bd_search_elasticsearch", "bd_search_qdrant", "bd_search_neo4j", "bd_search_web",
            }
            result = await client.call_tool("bd_search_elasticsearch", {
                "book_id": "book-01",
                "run_id": "run-01",
                "query": "Ada Lovelace",
            })
            assert not result.is_error, result
            data = ai.bd.tools.retrieval.domain.to_plain_data(result.data)
            assert data["status"] == "complete", data
            assert data["evidence"][0]["id"] == "elasticsearch:1", data
            session = ai.bd.tools.retrieval.entities.RetrievalSession(
                book_id = "book-01", run_id = "run-01", source = "elasticsearch",
            )
            replies = await asyncio.gather(*(
                ai.bd.tools.retrieval.service.call_source_tool(
                    session,
                    f"query {index}",
                    source = "elasticsearch",
                )
                for index in range(3)
            ))
            assert session.tool_calls == 2
            assert sum('"status": "call_limit"' in reply for reply in replies) == 1
            search_tool = next(item for item in tools if item.name == "bd_search_elasticsearch")
            assert search_tool.annotations.readOnlyHint is True
            assert search_tool.annotations.destructiveHint is False
            params = mcp_bd.tools.search.params
            web_enabled = params.WEB_SEARCH_ENABLED
            params.WEB_SEARCH_ENABLED = False
            try:
                web_result = await client.call_tool("bd_search_web", {"query": "current events"})
                assert not web_result.is_error
                assert ai.bd.tools.retrieval.domain.to_plain_data(web_result.data)["status"] == "disabled"
            finally:
                params.WEB_SEARCH_ENABLED = web_enabled
    finally:
        domains.bd.rag.ai.retrieval.service.retrieve_source = original


async def check_partial_backend_statuses() -> None:
    retriever = domains.bd.rag.ai.retrieval.service
    original_read = retriever._read_index
    original_retrieve = retriever._retrieve_one_source

    async def fake_read_index(book_id, run_id):
        return {"agentic_run_id": run_id}

    async def fake_retrieve(source, book_id, query, index, top_k):
        if source == "neo4j":
            raise TimeoutError
        return [{
            "id": f"{source}:1",
            "text": f"Evidence from {source}",
            "score": 1,
            "source": source,
            "citation": {"book_id": book_id},
        }]

    retriever._read_index = fake_read_index
    retriever._retrieve_one_source = fake_retrieve
    try:
        result = await retriever.retrieve_sources("book-01", "run-01", "question")
        assert result["elasticsearch"]["status"] == "complete"
        assert result["qdrant"]["status"] == "complete"
        assert result["neo4j"]["status"] == "timeout"
        assert len(retriever.fuse_evidence([
            result["elasticsearch"]["evidence"], result["qdrant"]["evidence"],
        ])) == 2
    finally:
        retriever._read_index = original_read
        retriever._retrieve_one_source = original_retrieve


async def check_book_authorization() -> None:
    service = mcp_bd.tools.search.service
    params = mcp_bd.tools.search.params
    original_profile = params.MCP_PROFILE
    original_ids = params.DEMO_BOOK_IDS
    original_token_reader = service.get_access_token
    try:
        params.MCP_PROFILE = "local_demo"
        params.DEMO_BOOK_IDS = frozenset({"book-01"})
        await service.authorize_book("book-01", False)
        try:
            await service.authorize_book("book-02", False)
        except PermissionError:
            pass
        else:
            raise AssertionError("local demo accepted a book outside its corpus allowlist")

        params.MCP_PROFILE = "protected"
        service.get_access_token = lambda: SimpleNamespace(
            claims = {"bd_book_ids": ["book-01"]},
            scopes = ["bd.search.read"],
            subject = "demo-user",
            client_id = "demo-client",
        )
        await service.authorize_book("book-01", False)
        try:
            await service.authorize_book("book-02", False)
        except PermissionError:
            pass
        else:
            raise AssertionError("protected profile accepted a book outside JWT book scope")
    finally:
        params.MCP_PROFILE = original_profile
        params.DEMO_BOOK_IDS = original_ids
        service.get_access_token = original_token_reader


def check_protected_profile_configuration() -> None:
    params = auth_params
    names = ("JWT_JWKS_URI", "JWT_ISSUER", "JWT_AUDIENCE", "JWT_REQUIRED_SCOPE")
    original_profile = mcp_params.MCP_PROFILE
    original = {name: getattr(params, name) for name in names}
    try:
        mcp_params.MCP_PROFILE = "protected"
        params.JWT_JWKS_URI = "https://identity.example/.well-known/jwks.json"
        params.JWT_ISSUER = "https://identity.example/"
        params.JWT_AUDIENCE = "coelho-nexus-mcp"
        params.JWT_REQUIRED_SCOPE = "bd.search.read"
        assert isinstance(auth_service.build_auth_provider(mcp_params.MCP_PROFILE), JWTVerifier)
        assert isinstance(mcp_server_service.build_external_server().auth, JWTVerifier)
        params.JWT_JWKS_URI = ""
        try:
            auth_service.build_auth_provider(mcp_params.MCP_PROFILE)
        except RuntimeError as error:
            assert "BD_MCP_JWKS_URI" in str(error)
        else:
            raise AssertionError("protected profile accepted missing JWT verification settings")
    finally:
        mcp_params.MCP_PROFILE = original_profile
        for name, value in original.items():
            setattr(params, name, value)


async def check_local_demo_is_loopback_only() -> None:
    middleware_type = api_mcp_middleware.LocalDemoLoopbackMiddleware

    async def request(peer: str) -> list[dict]:
        sent = []

        async def inner(scope, receive, send):
            sent.append({"type": "http.response.start", "status": 200})
            sent.append({"type": "http.response.body", "body": b"ok"})

        async def receive():
            return {"type": "http.request", "body": b"", "more_body": False}

        async def send(message):
            sent.append(message)

        middleware = middleware_type(inner)
        await middleware({
            "type": "http", "method": "GET", "path": "/mcp", "scheme": "http",
            "headers": [], "client": (peer, 1234), "server": ("localhost", 8000),
            "http_version": "1.1",
        }, receive, send)
        return sent

    local_response = await request("127.0.0.1")
    remote_response = await request("203.0.113.10")
    assert local_response[0]["status"] == 200
    assert remote_response[0]["status"] == 403


def main() -> None:
    check_scope_domain()
    check_ask_book_retrieval_subgraph_compiles()
    check_specialist_agents_build()
    check_graph_fusion_preserves_sources()
    asyncio.run(check_mcp_tool_contract())
    asyncio.run(check_partial_backend_statuses())
    asyncio.run(check_book_authorization())
    asyncio.run(check_local_demo_is_loopback_only())
    check_protected_profile_configuration()
    print("BD Search MCP and retrieval contracts: ok")


if __name__ == "__main__":
    main()
