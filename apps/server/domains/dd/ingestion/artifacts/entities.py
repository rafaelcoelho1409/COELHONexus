from __future__ import annotations

from typing import Any, NamedTuple


class Artifact(NamedTuple):
    """One materialized media payload + its serving metadata."""
    name:         str    # `{sha256[:16]}.{ext}` — content-addressed
    data:         bytes
    content_type: str
    source_url:   str    # `"data:"` for inline payloads


class HtmlReference(NamedTuple):
    """One media reference found in an HTML page, to be rewritten once its artifact is stored."""
    element:  Any    # the BeautifulSoup tag that carries it
    attr:     str    # the attribute: `src`, `data-src`, `poster` or `srcset`
    kind:     str    # `data` (inline) or `remote`
    payload:  str    # the data URL, or the remote URL resolved against the page's
