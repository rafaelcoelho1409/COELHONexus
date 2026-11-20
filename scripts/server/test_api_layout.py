"""Check that a route's URL path follows the package path of its router.

Run with `cd apps/server && .venv/bin/python ../../scripts/server/test_api_layout.py`.
Features with sub-packages mount them through their router, so the tree on disk and URL tree stay aligned.
This check builds the real app and reads its routes without network access."""

import os
import sys
from pathlib import Path

from fastapi.routing import APIRoute

for _name, _value in {
    "REDIS_HOST": "localhost",
    "REDIS_PORT": "6379",
    "REDIS_PASSWORD": "",
    "ENVIRONMENT": "local",
}.items():
    os.environ.setdefault(_name, _value)
sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "apps" / "server"))

from main import app  # noqa: E402

API_PREFIX = "entrypoints.api.v1."
CONTEXTS = ("bd", "ycs", "settings")


def expected_prefix(route: APIRoute) -> str | None:
    """`/api/v1/<package path>` of the module defining the route, or None when it is not under `entrypoints.api.v1`."""
    module = route.endpoint.__module__
    if not module.startswith(API_PREFIX):
        return None
    package = module.removeprefix(API_PREFIX).rsplit(".", 1)[
        0
    ]  # drop the file (`router`), keep the package path
    return "/api/v1/" + package.replace(".", "/")


def main() -> None:
    problems: list[str] = []
    seen = 0
    for route in app.routes:
        if (
            not isinstance(route, APIRoute)
            or not route.include_in_schema
            or not any(route.path.startswith(f"/api/v1/{ctx}/") for ctx in CONTEXTS)
        ):
            continue
        prefix = expected_prefix(route)
        if prefix is None:
            continue
        seen += 1
        if route.path != prefix and not route.path.startswith(prefix + "/"):
            problems.append(
                f"{sorted(route.methods)[0]} {route.path}  is defined in {route.endpoint.__module__}  (expected under {prefix})"
            )
    for line in problems:
        print(line)
    print(
        f"api layout: {'FAIL' if problems else 'ok'} ({seen} route(s) checked, {len(problems)} violation(s))"
    )
    if problems:
        sys.exit(1)


if __name__ == "__main__":
    main()
