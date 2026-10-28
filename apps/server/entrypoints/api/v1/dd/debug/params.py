"""debug router — tunables + lookup tables."""
from __future__ import annotations
import domains


# `tier=N` of the debug route is the Nth source kind in the dispatcher's order of preference.
KIND_BY_TIER = dict(enumerate(domains.dd.ingestion.runtime.dispatch.params.KIND_PRIORITY, start = 1))
