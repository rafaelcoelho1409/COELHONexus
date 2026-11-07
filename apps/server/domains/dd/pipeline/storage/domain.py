"""Identity of the per-item results the fan-out nodes store."""
from __future__ import annotations
from . import params

import hashlib


def digest(*parts: str) -> str:
    """A short content digest of the parts, in order: the identity of a per-item result."""
    h = hashlib.sha256()
    for part in parts:
        h.update(part.encode("utf-8", "replace"))
        h.update(b"\x1f")
    return h.hexdigest()[:params.DIGEST_CHARS]
