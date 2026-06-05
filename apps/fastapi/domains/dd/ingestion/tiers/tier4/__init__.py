"""Tier 4 — httpx-first docs crawler with Crawl4AI Playwright fallback.

Public surface is just `run`. Sphinx discovery primitives + the Playwright
fallback are tier4-internal — exposed as `sphinx/` / `playwright.py` submodules
for the `run` function's own use.
"""
from __future__ import annotations

from .run import run

__all__ = ["run"]
