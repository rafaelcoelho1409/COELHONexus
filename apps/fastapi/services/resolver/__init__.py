"""
Resolver — curated-list lookup over apps/fastapi/files/sources.yaml.

Tier ranking (best first):
  1: llms_full_txt   single-fetch full corpus, LLM-ready
  2: llms_txt        TOC index pointing to per-section URLs
  3: sitemap_xml     authoritative URL list (crawl seed)
  4: docs_url        root, recursive crawl fallback

There is no online discovery, no fuzzy matching, no search, no heuristics.
If a name is not in the YAML, lookup returns None — callers must reject
the request with "not in catalog".
"""

from .sources import (
    SourceEntry,
    TierUrl,
    bootstrap_sources,
    known_names,
    list_sources,
    load_sources,
    lookup,
)

__all__ = [
    "SourceEntry",
    "TierUrl",
    "bootstrap_sources",
    "known_names",
    "list_sources",
    "load_sources",
    "lookup",
]
