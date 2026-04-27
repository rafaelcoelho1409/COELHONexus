"""
Catalog loader — reads apps/fastapi/sources.yaml at startup.

The catalog is the FIRST source of truth: any framework with a curated entry
skips GitHub Search + ecosyste.ms entirely. Hand-edited YAML lives next to
the FastAPI app so it ships in the Docker image — no ConfigMap, no volume.

Schema per entry (only `name` is required; everything else optional):
  name          str           canonical name
  aliases       list[str]     alternative names (case-insensitive)
  docs_url      str           canonical docs root
  repo_url      str           official source repo
  llms_full_txt str           explicit URL when published
  llms_txt      str           same; index of pointers
  sitemap_xml   str           override when sitemap is non-conventional
  notes         str           free-form
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from threading import Lock
from typing import Optional

logger = logging.getLogger(__name__)

# Catalog file lives at apps/fastapi/sources.yaml — same level as app.py so
# the Docker image bundles it and both fastapi + celery containers see it.
_CATALOG_PATH = Path(__file__).parent.parent.parent / "sources.yaml"


@dataclass
class CatalogEntry:
    name: str
    aliases: list[str] = field(default_factory=list)
    docs_url: Optional[str] = None
    repo_url: Optional[str] = None
    llms_full_txt: Optional[str] = None
    llms_txt: Optional[str] = None
    sitemap_xml: Optional[str] = None
    notes: Optional[str] = None


_index: dict[str, CatalogEntry] | None = None  # lower-cased name/alias → entry
_index_lock = Lock()


def _build_index() -> dict[str, CatalogEntry]:
    if not _CATALOG_PATH.exists():
        logger.warning(
            f"[resolver.catalog] sources.yaml missing at {_CATALOG_PATH} "
            "— catalog override disabled"
        )
        return {}
    try:
        import yaml
    except ImportError:
        logger.warning("[resolver.catalog] PyYAML not installed — catalog disabled")
        return {}

    try:
        data = yaml.safe_load(_CATALOG_PATH.read_text(encoding="utf-8"))
    except yaml.YAMLError as e:
        logger.error(f"[resolver.catalog] YAML parse error: {e}")
        return {}

    raw_entries = (data or {}).get("frameworks", []) if isinstance(data, dict) else []
    if not isinstance(raw_entries, list):
        logger.error(f"[resolver.catalog] expected list under 'frameworks' key")
        return {}

    index: dict[str, CatalogEntry] = {}
    for raw in raw_entries:
        if not isinstance(raw, dict) or not raw.get("name"):
            continue
        entry = CatalogEntry(
            name=str(raw["name"]).strip(),
            aliases=[str(a).strip() for a in (raw.get("aliases") or []) if a],
            docs_url=raw.get("docs_url"),
            repo_url=raw.get("repo_url"),
            llms_full_txt=raw.get("llms_full_txt"),
            llms_txt=raw.get("llms_txt"),
            sitemap_xml=raw.get("sitemap_xml"),
            notes=raw.get("notes"),
        )
        # Index by canonical name + every alias (case-insensitive).
        for key in [entry.name, *entry.aliases]:
            index[key.lower().strip()] = entry
    logger.info(
        f"[resolver.catalog] loaded {len(raw_entries)} frameworks "
        f"({len(index)} index keys including aliases)"
    )
    return index


def load_catalog() -> dict[str, CatalogEntry]:
    """Lazy-init thread-safe accessor. Call at startup to warm the cache."""
    global _index
    if _index is not None:
        return _index
    with _index_lock:
        if _index is None:
            _index = _build_index()
    return _index


def lookup_catalog(name: str) -> Optional[CatalogEntry]:
    """Case-insensitive exact match against canonical names + aliases."""
    if not name:
        return None
    return load_catalog().get(name.lower().strip())


def fuzzy_lookup_catalog(query: str, score_threshold: int = 85) -> Optional[CatalogEntry]:
    """
    Fuzzy-match `query` against catalog names + aliases via rapidfuzz.
    Used as fallback for short bare queries that NER returns 0 entities for
    (e.g., user types just "FastApi" or "PyDantic" or "Tensorflow").

    Returns the best match above `score_threshold` (0-100) or None.
    """
    if not query or not query.strip():
        return None
    try:
        from rapidfuzz import process, fuzz
    except ImportError:
        logger.warning(
            "[resolver.catalog] rapidfuzz not installed — fuzzy match disabled"
        )
        return None

    index = load_catalog()
    if not index:
        return None

    keys = list(index.keys())
    best = process.extractOne(
        query.lower().strip(),
        keys,
        scorer=fuzz.WRatio,
        score_cutoff=score_threshold,
    )
    if best is None:
        return None
    matched_key = best[0]
    return index.get(matched_key)
