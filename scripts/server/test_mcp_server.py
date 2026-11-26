"""Offline checks for the embedded MCP server (no cluster, no external services): the real `main:app` answers MCP
over Streamable HTTP at `/mcp`, and serving it leaves FastAPI's 404 / 405 / trailing-slash redirect behaviour
untouched. Only the lifespan is swapped (the real one needs MinIO, Postgres, Neo4j, ...); routes are the real ones.

    cd apps/server && .venv/bin/python ../../scripts/server/test_mcp_server.py"""
import asyncio
import os
import socket
import sys
import threading
import time
from contextlib import asynccontextmanager
from pathlib import Path

for name, value in {"REDIS_HOST": "localhost", "REDIS_PORT": "6379", "REDIS_PASSWORD": "x", "ENVIRONMENT": "test"}.items():
    os.environ.setdefault(name, value)  # infra.celery reads these at import; nothing connects

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "apps" / "server"))
import entrypoints  # noqa: E402
import main as nexus  # noqa: E402
from entrypoints.mcp.components import bd as mcp_bd  # noqa: E402

import httpx  # noqa: E402
import uvicorn  # noqa: E402
from fastmcp import Client  # noqa: E402
from fastmcp.utilities.lifespan import combine_lifespans  # noqa: E402


@asynccontextmanager
async def light_lifespan(app):
    yield


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def check_edge_settings() -> None:
    domain = entrypoints.api.domain
    assert domain.parse_csv(" a, b,,") == ["a", "b"]
    assert domain.parse_csv("") == []
    assert entrypoints.api.params.CORS_ORIGINS == ["*"] or "CORS_ORIGINS" in os.environ
    assert hasattr(mcp_bd, "registry")


async def run_checks(base: str) -> None:
    async with Client(f"{base}/mcp") as client:
        tools = await client.list_tools()
        expected = [
            "bd_ingestion_get_run",
            "bd_ingestion_get_quality_summary",
            "bd_ingestion_start_run",
            "bd_ingestion_resume_run",
            "bd_ingestion_cancel_run",
            "bd_ingestion_replay_from_node",
            "bd_rag_index_get_run",
            "bd_rag_index_get_current",
            "bd_rag_index_start",
            "bd_rag_index_resume",
            "bd_rag_index_cancel",
            "bd_rag_index_replay_from_node",
            "bd_search_elasticsearch",
            "bd_search_qdrant",
            "bd_search_neo4j",
            "bd_search_web",
        ]
        assert {t.name for t in tools} == set(expected), tools

    async with httpx.AsyncClient(base_url=base) as http:
        r = await http.get("/health")
        assert r.status_code == 200 and r.json()["status"] == "healthy", r.text
        r = await http.get("/nope")
        assert r.status_code == 404 and r.json() == {"detail": "Not Found"}, (r.status_code, r.text)
        r = await http.post("/health")
        assert r.status_code == 405, r.status_code
        r = await http.get("/health/")
        assert r.status_code == 307 and r.headers["location"].endswith("/health"), (r.status_code, r.headers)
        r = await http.get("/")
        assert r.json()["endpoints"]["mcp"] == "/mcp", r.text


def main() -> None:
    check_edge_settings()
    print("edge settings: ok")

    nexus.app.router.lifespan_context = combine_lifespans(light_lifespan, nexus.mcp_app.lifespan)
    port = free_port()
    server = uvicorn.Server(uvicorn.Config(nexus.app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    for _ in range(100):
        if server.started:
            break
        time.sleep(0.1)
    assert server.started, "uvicorn did not start"
    try:
        asyncio.run(run_checks(f"http://127.0.0.1:{port}"))
    finally:
        server.should_exit = True
        thread.join(timeout=10)
    print("app /mcp + FastAPI behaviour: ok")


if __name__ == "__main__":
    main()
    print("OK")
