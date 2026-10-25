"""api domain — pure helpers: env-value parsing and OpenAPI naming (no I/O, deterministic)."""
from __future__ import annotations

import re
from typing import Literal

from fastapi.routing import APIRoute


def parse_csv(raw: str) -> list[str]:
    """Split a comma-separated env value, dropping blanks and whitespace."""
    return [v.strip() for v in raw.split(",") if v.strip()]


def host_origin_protection(allowed_hosts: list[str]) -> bool | Literal["auto"]:
    """Strict Host/Origin validation once an allow-list is configured,
    FastMCP's loopback-aware "auto" mode otherwise."""
    return True if allowed_hosts else "auto"


def operation_id(route: APIRoute) -> str:
    """OpenAPI `operationId` of a route: `<method>_<path>`, e.g. `get_dd_runs_active` for
    `GET /api/v1/dd/runs/active`. Typed clients (the web app's generated SDK) name their functions after it,
    so it must be readable, stable and unique — a method and a path are unique by construction, which a
    `<tag>-<function name>` scheme is not (two routes named `get_page` collide)."""
    method = sorted(route.methods or {"get"})[0].lower()
    path = re.sub(r"[{}]", "", route.path_format.replace(":path", "")).removeprefix("/api/v1")
    slug = re.sub(r"[^0-9a-zA-Z]+", "_", path).strip("_") or "root"
    return f"{method}_{slug}"
