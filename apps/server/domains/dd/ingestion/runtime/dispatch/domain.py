"""Pure helpers (no I/O) — best-source pick from a catalog entry."""
from __future__ import annotations
from . import params



def pick_best(entry: dict) -> dict | None:
    for kind in params.KIND_PRIORITY:
        if entry.get(kind):
            return {"kind": kind, "url": entry[kind]}
    return None


def source_chain(entry: dict) -> list[dict]:
    """Every source the catalog entry declares, best first — `[{kind, url}, ...]`. The dispatcher walks it: the first
    entry is the plan, the rest are what it falls back on when a tier turns out to be unusable."""
    return [{"kind": kind, "url": entry[kind]} for kind in params.KIND_PRIORITY if entry.get(kind)]
