"""Identifier registries for the arXiv tool — per docs/CODE-CONVENTIONS.md §2.

The conventions reserve `keys.py` for string identifier registries (prefixes,
tuple-of-strings name lists, derived index dicts, and `*_key()` builder
functions). The XML namespace map below is structurally a registry of
identifiers (prefix → URI) used by the Atom parser in domain.py; same shape
as e.g. `DD_PROCESSES` / `CONTEXT_PROVIDERS` in llm/rotator/bandit/keys.py.
"""
from __future__ import annotations


# Namespaces emitted by the arXiv Atom feed. Used by ElementTree's
# namespace-aware element matching in domain.parse_atom_feed (e.g.
# `.find("atom:title", ATOM_NAMESPACES)`). These URIs are stable per the
# Atom 1.0 spec + arXiv's own schema; treat as fixed.
ATOM_NAMESPACES: dict[str, str] = {
    "atom":       "http://www.w3.org/2005/Atom",
    "arxiv":      "http://arxiv.org/schemas/atom",
    "opensearch": "http://a9.com/-/spec/opensearch/1.1/",
}
