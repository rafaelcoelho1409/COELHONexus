"""
Curated-sources resolver — replaces the old name→URL search pipeline.

Reads `apps/fastapi/files/sources.yaml` once at startup and provides
case-insensitive O(1) lookup of any tracked technology by name. For each
hit, returns docs URLs in tier order:

  Tier 1: llms_full_txt    single-fetch full corpus, LLM-ready (gold standard)
  Tier 2: llms_txt         TOC index pointing to per-section URLs
  Tier 3: sitemap_xml      authoritative URL list (crawl seed)
  Tier 4: docs_url         root, recursive crawl fallback

Plus optional `github_repo` for source mining and `category` semantic tag.

There is no online discovery, no fuzzy matching, no LLM, no heuristics.
If a name is not in the YAML, lookup returns None and the caller must
reject the request with "not in catalog".
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import yaml

logger = logging.getLogger(__name__)

# Resolved relative to this file: apps/fastapi/services/resolver/sources.py
# parents[2] = apps/fastapi → files/sources.yaml.
_SOURCES_FILE = Path(__file__).resolve().parents[2] / "files" / "sources.yaml"

# YAML section name → tier rank (lower = better).
_TIER_ORDER: list[tuple[str, int]] = [
    ("llms_full_txt", 1),
    ("llms_txt",      2),
    ("sitemap_xml",   3),
    ("docs_url",      4),
]


@dataclass
class TierUrl:
    tier: int
    kind: str
    url: str


_CATEGORY_TO_LANGUAGE: dict[str, str] = {
    "python": "python",
    "go": "go",
    "rust": "rust",
    "bash": "bash",
}

# Self-referential entries: framework name == language name. These set
# `language` from `name` directly (e.g., entry "Python" → language "python").
_NAME_IS_LANGUAGE: set[str] = {"python", "bash", "go", "rust"}


@dataclass
class SourceEntry:
    name: str
    category: Optional[str] = None
    tiers: list[TierUrl] = field(default_factory=list)
    github_repo: Optional[str] = None

    @property
    def best(self) -> Optional[TierUrl]:
        return self.tiers[0] if self.tiers else None

    @property
    def available_tier_kinds(self) -> list[str]:
        return [t.kind for t in self.tiers]

    @property
    def language(self) -> Optional[str]:
        """
        Derived programming-language scope, used by downstream filters
        (e.g., `_build_language_filter` in services/knowledge/ingestion.py
        for polyglot frameworks). Replaces the old LLM-based scope gate.

        Rules (deterministic):
          1. Entry name itself is a language ("Python", "Go ", "Rust", "Bash")
             → language = name lowercased.
          2. category == "Python" → language = "python" (covers ~90% of
             entries; the "(Python)" name suffixes in the catalog already
             scope these to Python clients of polyglot tools).
          3. category matches a known language name → language = that name.
          4. Otherwise → None (polyglot or non-code-language entry like
             Docker, Kubernetes, Helm, Terraform).
        """
        norm_name = (self.name or "").strip().lower()
        if norm_name in _NAME_IS_LANGUAGE:
            return norm_name
        if not self.category:
            return None
        cat = self.category.strip().lower()
        return _CATEGORY_TO_LANGUAGE.get(cat)

    @property
    def github_org_repo(self) -> tuple[Optional[str], Optional[str]]:
        """Parse github_repo URL → (org, name). (None, None) on bad shape."""
        if not self.github_repo or "github.com/" not in self.github_repo:
            return None, None
        try:
            parts = self.github_repo.rstrip("/").split("/")
            if len(parts) < 5 or parts[2].lower() != "github.com":
                return None, None
            org = parts[3] or None
            name = parts[4].removesuffix(".git") or None
            return org, name
        except Exception:
            return None, None


_BY_NAME: dict[str, SourceEntry] = {}
_CANONICAL_NAMES: list[str] = []


def _normalize(name: str) -> str:
    return (name or "").strip().lower()


def load_sources(path: Path | None = None) -> dict[str, SourceEntry]:
    """Parse sources.yaml → {normalized_name: SourceEntry}. Empty on error."""
    p = path or _SOURCES_FILE
    if not p.is_file():
        logger.warning(f"[sources] file not found: {p}")
        return {}

    try:
        with p.open() as f:
            raw = yaml.safe_load(f) or {}
    except yaml.YAMLError as e:
        logger.error(f"[sources] YAML parse error in {p}: {e}")
        return {}

    if not isinstance(raw, dict):
        return {}

    index: dict[str, SourceEntry] = {}

    def _ensure(name: str) -> SourceEntry:
        clean = (name or "").strip()
        norm = _normalize(clean)
        e = index.get(norm)
        if e is None:
            e = SourceEntry(name=clean)
            index[norm] = e
        return e

    for section, tier in _TIER_ORDER:
        rows = raw.get(section) or []
        if not isinstance(rows, list):
            continue
        for row in rows:
            if not isinstance(row, dict):
                continue
            n = row.get("name")
            u = row.get("url")
            if not n or not u:
                continue
            entry = _ensure(n)
            entry.tiers.append(
                TierUrl(tier=tier, kind=section, url=u.strip())
            )

    for row in raw.get("github_repo") or []:
        if isinstance(row, dict) and row.get("name") and row.get("url"):
            _ensure(row["name"]).github_repo = row["url"].strip()

    for row in raw.get("categories") or []:
        if isinstance(row, dict) and row.get("name"):
            cat = row.get("category")
            entry = _ensure(row["name"])
            entry.category = (cat or "").strip() or None

    for e in index.values():
        e.tiers.sort(key=lambda t: t.tier)

    return index


def bootstrap_sources(path: Path | None = None) -> int:
    """Load sources.yaml into module cache. Returns # entries loaded."""
    global _BY_NAME, _CANONICAL_NAMES
    _BY_NAME = load_sources(path)
    _CANONICAL_NAMES = sorted(
        {e.name for e in _BY_NAME.values()}, key=str.lower,
    )
    return len(_BY_NAME)


def lookup(name: str) -> Optional[SourceEntry]:
    """Case-insensitive lookup. Returns None if not in the curated catalog."""
    return _BY_NAME.get(_normalize(name))


def list_sources() -> list[SourceEntry]:
    """All entries, sorted alphabetically (case-insensitive)."""
    return sorted(_BY_NAME.values(), key=lambda e: e.name.lower())


def known_names() -> list[str]:
    """Canonical-cased names in alphabetical order."""
    return list(_CANONICAL_NAMES)
