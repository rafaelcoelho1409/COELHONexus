"""Sphinx discovery: inventory.py parses objects.inv, nav.py does DOM toctree BFS, page_split.py splits autodoc pages into sub-pages."""
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
