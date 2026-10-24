"""api domain — pure env-value parsing (no I/O, deterministic)."""
from __future__ import annotations

from typing import Literal


def parse_csv(raw: str) -> list[str]:
    """Split a comma-separated env value, dropping blanks and whitespace."""
    return [v.strip() for v in raw.split(",") if v.strip()]


def host_origin_protection(allowed_hosts: list[str]) -> bool | Literal["auto"]:
    """Strict Host/Origin validation once an allow-list is configured,
    FastMCP's loopback-aware "auto" mode otherwise."""
    return True if allowed_hosts else "auto"
