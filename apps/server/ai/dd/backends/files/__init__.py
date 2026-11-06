"""A read-only virtual filesystem over text a node already holds (a chapter's sources, a section's code bank, the rendered chapters)."""
from __future__ import annotations

from . import domain, params, service

__all__ = ["domain", "params", "service"]
