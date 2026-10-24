from __future__ import annotations

from typing import NamedTuple


class Artifact(NamedTuple):
    """One materialized media payload + its serving metadata."""
    name:         str    # `{sha256[:16]}.{ext}` — content-addressed
    data:         bytes
    content_type: str
    source_url:   str    # `"data:"` for inline payloads
