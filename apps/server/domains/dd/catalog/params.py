"""Resolver — catalog source path + tier priority order."""
from __future__ import annotations

from pathlib import Path


# Highest → lowest priority for picking the best source per framework.
TIER_ORDER = ("llms_full", "llms_txt", "sitemap", "docs", "github")

SOURCES_PATH = Path(__file__).resolve().parent / "sources.yaml"
