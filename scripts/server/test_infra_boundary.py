"""Boundary check: a COELHO Cloud service's client library is imported only inside its own `infra/<service>/`
module; everything else reaches the service dotted through `infra.<service>.service`. Offline, pure AST/text scan.

    cd apps/server && .venv/bin/python ../../scripts/server/test_infra_boundary.py

Add a service to RULES once its `infra/` module owns the library (so far: celery, elasticsearch, langfuse, neo4j)."""
import ast
import sys
from pathlib import Path

SERVER = Path(__file__).resolve().parents[2] / "apps" / "server"

# service -> (top-level library names only its infra module may import, text only its infra module may contain)
RULES: dict[str, tuple[tuple[str, ...], tuple[str, ...]]] = {
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
    "neo4j": (
        ("neo4j", "langchain_neo4j"),
        ('environ["NEO4J_', 'environ.get("NEO4J_', 'getenv("NEO4J_', "bolt://",
         "database=infra.neo4j.params", "database = infra.neo4j.params"),
    ),
}

SKIP_DIRS = {".venv", "__pycache__", ".ruff_cache"}


def python_files():
    for path in SERVER.rglob("*.py"):
        if not SKIP_DIRS.intersection(path.relative_to(SERVER).parts):
            yield path


def violations(service: str, libs: tuple[str, ...], texts: tuple[str, ...]) -> list[str]:
    home = SERVER / "infra" / service
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
                    found.append(f"{path.relative_to(SERVER)}:{number}: uses `{text}` (use a helper in infra.{service}.service)")
    return found


def main() -> None:
    failed = False
    for service, (libs, texts) in RULES.items():
        found = violations(service, libs, texts)
        for line in found:
            print(f"[{service}] {line}")
        failed = failed or bool(found)
        print(f"{service}: {'FAIL' if found else 'ok'} ({len(found)} violation(s))")
    if failed:
        sys.exit(1)
    print("OK")


if __name__ == "__main__":
    main()
