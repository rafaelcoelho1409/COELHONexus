"""Export the slice of the API the web app consumes to `apps/web/openapi/openapi.json`.

    cd apps/server && .venv/bin/python ../../scripts/server/export_openapi.py          # write
    cd apps/server && .venv/bin/python ../../scripts/server/export_openapi.py --check  # fail on drift

The web app's typed client (`@hey-api/openapi-ts`) is generated from this committed file, so the spec is
reviewable in a diff and the web build never needs a running backend. What it contains:
  * only the product routes (`/api/v1/dd`, `/api/v1/ycs`, `/api/v1/settings`) — the Research Radar routes are
    being replaced and are left out;
  * `/health`, republished as `/api/health` (the web app proxies `/api/*`; its own `/health` is its probe);
  * only the schemas those routes still reference.
It refuses to write a spec that mentions the LLM Rotator: that project is private and must not leak into
the web app's committed client."""
import difflib
import json
import os
import re
import sys
from pathlib import Path

for _name, _value in {
    "REDIS_HOST": "localhost", "REDIS_PORT": "6379", "REDIS_PASSWORD": "", "ENVIRONMENT": "local", "DD_STUDY_SEM": "2",
}.items():
    os.environ.setdefault(_name, _value)
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "apps" / "server"))

from main import app  # noqa: E402

TARGET = Path(__file__).resolve().parents[2] / "apps" / "web" / "openapi" / "openapi.json"
INCLUDED_PREFIXES = ("/api/v1/dd", "/api/v1/ycs", "/api/v1/settings")
PRIVATE_TERMS = re.compile(r"rotator|litellm|bandit|fgts", re.IGNORECASE)


def references(node: object) -> set[str]:
    """Every `#/components/schemas/<Name>` referenced anywhere under `node`."""
    found: set[str] = set()
    if isinstance(node, dict):
        for key, value in node.items():
            if key == "$ref" and isinstance(value, str) and value.startswith("#/components/schemas/"):
                found.add(value.rsplit("/", 1)[1])
            else:
                found |= references(value)
    elif isinstance(node, list):
        for item in node:
            found |= references(item)
    return found


def build() -> dict:
    spec = app.openapi()
    paths: dict[str, dict] = {}
    for path, item in spec["paths"].items():
        if path == "/health":
            paths["/api/health"] = item
        elif path.startswith(INCLUDED_PREFIXES):
            paths[path] = item
    schemas = spec.get("components", {}).get("schemas", {})
    needed: set[str] = set()
    queue = list(references(paths))
    while queue:
        name = queue.pop()
        if name in needed or name not in schemas:
            continue
        needed.add(name)
        queue.extend(references(schemas[name]))
    out = {
        "openapi": spec["openapi"],
        "info": {"title": "COELHO Nexus API", "version": spec["info"]["version"]},
        "paths": dict(sorted(paths.items())),
        "components": {"schemas": {name: schemas[name] for name in sorted(needed)}},
    }
    text = json.dumps(out)
    leak = PRIVATE_TERMS.search(text)
    if leak:
        raise SystemExit(f"refusing to export: the spec mentions a private term ({leak.group(0)!r})")
    return out


def main() -> None:
    rendered = json.dumps(build(), indent=2, ensure_ascii=False, sort_keys=False) + "\n"
    if "--check" in sys.argv:
        current = TARGET.read_text() if TARGET.exists() else ""
        if current != rendered:
            diff = difflib.unified_diff(current.splitlines(), rendered.splitlines(), "committed", "backend", lineterm="", n=1)
            print("\n".join(list(diff)[:40]))
            raise SystemExit("openapi.json is out of date — run export_openapi.py and `npm run api:generate`")
        print(f"ok  {TARGET.relative_to(TARGET.parents[2])} matches the backend")
        return
    TARGET.parent.mkdir(parents=True, exist_ok=True)
    TARGET.write_text(rendered)
    spec = json.loads(rendered)
    print(f"wrote {TARGET} — {len(spec['paths'])} paths, {len(spec['components']['schemas'])} schemas")


if __name__ == "__main__":
    main()
