"""Resolver — curated framework catalog from `sources.yaml`."""
from .domain import pick_best_source, slugify
from .params import SOURCES_PATH, TIER_ORDER
from .service import index_by_slug, load_catalog


__all__ = [
    "SOURCES_PATH",
    "TIER_ORDER",
    "index_by_slug",
    "load_catalog",
    "pick_best_source",
    "slugify",
]
