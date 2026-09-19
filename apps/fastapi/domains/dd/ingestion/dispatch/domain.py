"""Pure helpers (no I/O) — best-source pick from a catalog entry."""
from __future__ import annotations
from . import params



def pick_best(entry: dict) -> dict | None:
    for kind in params.KIND_PRIORITY:
        if entry.get(kind):
            return {"kind": kind, "url": entry[kind]}
    return None
