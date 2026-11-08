"""Pipeline verify — checks on the code a chapter teaches: it parses, its imports resolve, a configuration file is valid, and, with a sandbox
executor, it runs. A block is `verified`, `unverified` (nothing applies, or a library could not be checked) or `failed`; it is never passed silently."""
from __future__ import annotations

from . import entities, params, domain, service

__all__ = ["domain", "entities", "params", "service"]
