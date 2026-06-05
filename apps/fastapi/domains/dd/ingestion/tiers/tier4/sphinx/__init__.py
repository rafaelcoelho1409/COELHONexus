"""Sphinx-specific discovery + content-transform primitives — used by tier4.run
to extract Sphinx/readthedocs sites comprehensively.

  entities.py    — Inventory + InventoryEntity + SubPage value objects
  inventory.py   — parse `objects.inv` (canonical entity catalog)
  nav.py         — DOM-based toctree discovery
  page_split.py  — split autodoc-bundled pages into per-entity sub-pages
"""
from __future__ import annotations

from .entities import Inventory, InventoryEntity, SubPage
from .inventory import fetch_inventory
from .nav import discover_via_toctree
from .page_split import maybe_split_page

__all__ = [
    "Inventory",
    "InventoryEntity",
    "SubPage",
    "discover_via_toctree",
    "fetch_inventory",
    "maybe_split_page",
]
