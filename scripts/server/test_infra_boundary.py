"""Boundary check: a COELHO Cloud service's client library is imported only inside its own `infra/<service>/`
module; everything else reaches the service dotted through `infra.<service>.service`. Offline, pure AST/text scan.

    cd apps/server && .venv/bin/python ../../scripts/server/test_infra_boundary.py

Add a service to RULES once its `infra/` module owns the library (so far: celery, elasticsearch, langfuse, neo4j,
minio, opentelemetry, postgres, qdrant, redis)."""
import ast
import sys
from pathlib import Path

SERVER = Path(__file__).resolve().parents[2] / "apps" / "server"

# Rule name whose texts any module under `infra/` may contain (not one service's home): client spans are built by
# `infra.opentelemetry.service.client_span` / `db_span` and the per-service `span` helpers, never by hand in a domain.
SHARED_RULE = "client-spans"

# service -> (top-level library names only its infra module may import, text only its infra module may contain)
RULES: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] = {
    SHARED_RULE: ((), ('"db.system"', '"db.operation"', '"aws.s3.bucket"', 'f"db.')),
    "celery": (("celery",), (".service.app.control",)),
    "elasticsearch": (
        ("elasticsearch", "elastic_transport"),
        ('environ["ELASTICSEARCH_', 'environ.get("ELASTICSEARCH_', 'getenv("ELASTICSEARCH_',
         "coelhonexus-youtube-metadata", "coelhonexus-youtube-transcriptions"),
    ),
    "langfuse": (
        ("langfuse",),
        ('environ["LANGFUSE_', 'environ.get("LANGFUSE_', 'getenv("LANGFUSE_',
         '"coelho.langfuse.', '"langfuse.', "'coelho.langfuse.", "'langfuse."),
    ),
    "minio": (
        ("aioboto3", "aiobotocore", "boto3", "botocore"),
        ('environ["MINIO_', 'environ.get("MINIO_', 'getenv("MINIO_',
         'environ["AWS_', 'environ.get("AWS_', 'getenv("AWS_', "s3v4"),
    ),
    "neo4j": (
        ("neo4j", "langchain_neo4j"),
        ('environ["NEO4J_', 'environ.get("NEO4J_', 'getenv("NEO4J_', "bolt://",
         "database=infra.neo4j.params", "database = infra.neo4j.params"),
    ),
    "opentelemetry": (
        ("opentelemetry",),
        ('environ["OTEL_', 'environ.get("OTEL_', 'getenv("OTEL_', '"celery.task_id"'),
    ),
    "playwright": (
        ("playwright", "crawl4ai"),
        ('environ["PLAYWRIGHT_', 'environ.get("PLAYWRIGHT_', 'getenv("PLAYWRIGHT_',
         'environ["BROWSER_', 'environ.get("BROWSER_', 'getenv("BROWSER_'),
    ),
    "postgres": (
        ("psycopg", "psycopg_pool", "psycopg2", "asyncpg"),
        ('environ["POSTGRES_', 'environ.get("POSTGRES_', 'getenv("POSTGRES_',
         "postgresql://", "langgraph.checkpoint.postgres"),
    ),
    "qdrant": (
        ("qdrant_client", "langchain_qdrant"),
        ('environ["QDRANT_', 'environ.get("QDRANT_', 'getenv("QDRANT_', "localhost:6333"),
    ),
    "redis": (
        ("redis",),
        ('environ["REDIS_', 'environ.get("REDIS_', 'getenv("REDIS_', "redis://",
         "socket_connect_timeout", "socket_timeout"),
    ),
}

SKIP_DIRS = {".venv", "__pycache__", ".ruff_cache"}


def python_files():
    for path in SERVER.rglob("*.py"):
        if not SKIP_DIRS.intersection(path.relative_to(SERVER).parts):
            yield path


def violations(service: str, libs: tuple[str, ...], texts: tuple[str, ...]) -> list[str]:
    home = SERVER / "infra" / ("" if service == SHARED_RULE else service)
    hint = "the infra span helpers" if service == SHARED_RULE else f"a helper in infra.{service}.service"
    found = []
    for path in python_files():
        if home in path.parents:
            continue
        source = path.read_text()
        for node in ast.walk(ast.parse(source)):
            names = []
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                names = [node.module]
            for name in names:
                if name.split(".")[0] in libs:
                    found.append(f"{path.relative_to(SERVER)}:{node.lineno}: imports `{name}` (use infra.{service}.service)")
        for number, line in enumerate(source.splitlines(), 1):
            for text in texts:
                if text in line and not line.lstrip().startswith("#"):
                    found.append(f"{path.relative_to(SERVER)}:{number}: uses `{text}` (use {hint})")
    return found


def lines_with(root: Path, texts: tuple[str, ...], *, skip_names: tuple[str, ...] = (), hint: str) -> list[str]:
    """Every non-comment line under `root` containing one of `texts` (files named in `skip_names` are exempt)."""
    found = []
    for path in python_files():
        if root not in path.parents or path.name in skip_names:
            continue
        for number, line in enumerate(path.read_text().splitlines(), 1):
            for text in texts:
                if text in line and not line.lstrip().startswith("#"):
                    found.append(f"{path.relative_to(SERVER)}:{number}: uses `{text}` ({hint})")
    return found


def env_outside_params() -> list[str]:
    """Env vars are read once, in a module's `params.py`; the rest of `infra/` reads `params.X`."""
    return lines_with(
        SERVER / "infra", ("os.environ", "os.getenv", "environ.get(", "from os import environ"), skip_names = ("params.py",),
        hint = "read it in this module's params.py",
    )


def entrypoint_redis() -> list[str]:
    """Entrypoints (routers) never open Redis clients or know key layouts: they call the domain services."""
    return lines_with(
        SERVER / "entrypoints",
        ("new_redis", '"coelhonexus:', "'coelhonexus:", ".scan_iter(", ".smembers(", ".sadd(", ".srem(", ".hgetall(", ".lrange("),
        hint = "a function in the domain's runtime/dispatch service",
    )


def llm_counter_homes() -> list[str]:
    """LLM-usage accounting lives in `domains/settings/llm_counter/` only. (`rr` keeps its own until rr is removed.)"""
    found = [
        f"{path.relative_to(SERVER)}: a per-context llm_counter (use domains.settings.llm_counter)"
        for path in sorted((SERVER / "domains").glob("*/llm_counter"))
        if path.parent.name not in ("settings", "rr")
    ]
    found += lines_with(
        SERVER, ("domains.dd.llm_counter", "domains.ycs.llm_counter"), skip_names = (), hint = "domains.settings.llm_counter",
    )
    return found


EXTRA_CHECKS = {
    "env-params": env_outside_params,
    "entrypoint-redis": entrypoint_redis,
    "llm-counter": llm_counter_homes,
}


def main() -> None:
    failed = False
    for service, (libs, texts) in RULES.items():
        found = violations(service, libs, texts)
        for line in found:
            print(f"[{service}] {line}")
        failed = failed or bool(found)
        print(f"{service}: {'FAIL' if found else 'ok'} ({len(found)} violation(s))")
    for name, check in EXTRA_CHECKS.items():
        found = check()
        for line in found:
            print(f"[{name}] {line}")
        failed = failed or bool(found)
        print(f"{name}: {'FAIL' if found else 'ok'} ({len(found)} violation(s))")
    if failed:
        sys.exit(1)
    print("OK")


if __name__ == "__main__":
    main()
