"""A/B of how a captured source becomes pages: arm A is the tier code as it is, arm B whatever is registered next to it
(`params.ARM_MODULES`). Same inputs for every arm, measured by the same code, judged by a rule written before any B ran."""
from __future__ import annotations
from . import current, domain, entities, params, patterns, service


__all__ = ["current", "domain", "entities", "params", "patterns", "service"]
