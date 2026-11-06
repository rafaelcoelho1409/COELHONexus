"""sawc_derive: a runnable example for each documentation block that is only a bare signature, by a code_deriver agent that checks its code with a parser tool."""
from __future__ import annotations

from . import domain, keys, params, patterns, schemas, versions
from . import service, node

__all__ = ["domain", "keys", "node", "params", "patterns", "schemas", "service", "versions"]
