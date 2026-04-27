"""
llms.txt directory mirror — Layer 0b (zero-cost lookup).

Source: thedaviddias/llms-txt-hub (MIT). 642 verified entries as of
2026-04-26 — projects whose maintainers explicitly published llms.txt.
Strongest possible signal: publisher-asserted.

Schema (per `websites.json` entry):
  {
    "name":            "Pydantic",
    "domain":          "https://docs.pydantic.dev",     ← canonical docs root
    "description":     "...",
    "llmsTxtUrl":      "https://docs.pydantic.dev/llms.txt",      (optional)
    "llmsFullTxtUrl":  "https://docs.pydantic.dev/llms-full.txt", (optional)
    "category":        "ai-ml",
    ...
  }

Refresh strategy:
  - `bootstrap()` fetches once on FastAPI startup (lifespan) — index in memory
  - background asyncio task re-fetches every 24h (no K8s cron needed)
  - graceful degradation: if GitHub unreachable, lookup_llmstxt() returns
    None for every name; resolver falls through to ecosyste.ms / search

Cost: 277 KB JSON × 1 fetch / day / pod. GitHub raw is unauthenticated +
unmetered for git/raw payloads; well below any threshold.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
from dataclasses import dataclass
from threading import Lock
from typing import Optional

import httpx

logger = logging.getLogger(__name__)


_HUB_URL = os.environ.get(
    "LLMSTXT_HUB_URL",
    "https://raw.githubusercontent.com/thedaviddias/llms-txt-hub/main/data/websites.json",
)
_FETCH_TIMEOUT_SEC = 15.0
_REFRESH_INTERVAL_SEC = 24 * 60 * 60  # 24h
_USER_AGENT = "COELHONexus-resolver/1.0"


@dataclass
class LlmsTxtEntry:
    """One curated entry from llms-txt-hub."""
    name: str
    docs_url: Optional[str] = None         # canonical docs site root (`domain` field)
    llms_url: Optional[str] = None         # llms.txt URL (Markdown index)
    llms_full_url: Optional[str] = None    # llms-full.txt URL (whole bundle)
    category: Optional[str] = None
    source: str = "llmstxt-hub"


_index: dict[str, LlmsTxtEntry] = {}
_lock = Lock()
_loaded_at: float = 0.0
_n_entries: int = 0


def _normalize_key(s: str) -> str:
    """Lower + strip + collapse whitespace; alphanumeric-only fallback also indexed."""
    return re.sub(r"\s+", " ", (s or "").lower().strip())


def _alpha_key(s: str) -> str:
    """Strip everything non-alphanumeric for slug-style match (FastAPI ↔ fast-api)."""
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def _build_index_from_payload(payload: list[dict]) -> dict[str, LlmsTxtEntry]:
    """Index by `name` (normalized + alpha-only) — no aliases in the hub schema."""
    index: dict[str, LlmsTxtEntry] = {}
    for item in payload:
        if not isinstance(item, dict):
            continue
        name = (item.get("name") or "").strip()
        if not name:
            continue
        entry = LlmsTxtEntry(
            name=name,
            docs_url=item.get("domain") or None,
            llms_url=item.get("llmsTxtUrl") or None,
            llms_full_url=item.get("llmsFullTxtUrl") or None,
            category=item.get("category"),
        )
        for key in {_normalize_key(name), _alpha_key(name)}:
            if key:
                index[key] = entry
    return index


async def _fetch_payload(client: Optional[httpx.AsyncClient] = None) -> Optional[list[dict]]:
    """GET websites.json from the hub. None on any failure."""
    own_client = client is None
    if own_client:
        client = httpx.AsyncClient(
            headers={"User-Agent": _USER_AGENT, "Accept": "application/json"},
            timeout=_FETCH_TIMEOUT_SEC,
        )
    try:
        try:
            r = await client.get(_HUB_URL, timeout=_FETCH_TIMEOUT_SEC)
        except httpx.HTTPError as e:
            logger.warning(f"[resolver.llmstxt] fetch error: {type(e).__name__}: {e}")
            return None
        if r.status_code != 200:
            logger.warning(f"[resolver.llmstxt] fetch HTTP {r.status_code}")
            return None
        try:
            data = r.json()
        except (ValueError, json.JSONDecodeError) as e:
            logger.warning(f"[resolver.llmstxt] JSON parse error: {e}")
            return None
        if not isinstance(data, list):
            logger.warning("[resolver.llmstxt] unexpected payload shape (not a list)")
            return None
        return data
    finally:
        if own_client and client is not None:
            await client.aclose()


async def refresh_llmstxt(client: Optional[httpx.AsyncClient] = None) -> int:
    """
    One-shot refresh — atomically replace the in-memory index from the hub.
    Returns number of entries loaded; 0 on any failure (keeps prior index).
    """
    global _index, _loaded_at, _n_entries
    payload = await _fetch_payload(client=client)
    if not payload:
        return 0
    new_index = _build_index_from_payload(payload)
    if not new_index:
        return 0
    with _lock:
        _index = new_index
        _n_entries = len(payload)
        _loaded_at = asyncio.get_event_loop().time()
    logger.info(
        f"[resolver.llmstxt] loaded {_n_entries} entries "
        f"({len(new_index)} index keys) from {_HUB_URL}"
    )
    return _n_entries


async def bootstrap() -> int:
    """
    FastAPI lifespan hook — fetch ONCE at startup. Safe to call from
    lifespan handler; does not block on failure (returns 0).
    """
    return await refresh_llmstxt()


async def refresh_loop(interval_sec: float = _REFRESH_INTERVAL_SEC) -> None:
    """
    Background task — sleeps `interval_sec` then re-fetches, forever.
    Cancellation-safe: catches CancelledError to exit cleanly on shutdown.
    """
    try:
        while True:
            await asyncio.sleep(interval_sec)
            try:
                n = await refresh_llmstxt()
                if n:
                    logger.info(f"[resolver.llmstxt] periodic refresh ok ({n} entries)")
                else:
                    logger.warning("[resolver.llmstxt] periodic refresh failed (kept prior index)")
            except Exception as e:
                logger.warning(f"[resolver.llmstxt] refresh loop error: {type(e).__name__}: {e}")
    except asyncio.CancelledError:
        logger.info("[resolver.llmstxt] refresh loop cancelled")
        raise


def lookup_llmstxt(name: str) -> Optional[LlmsTxtEntry]:
    """O(1) case-insensitive lookup. Tries normalized then alpha-only key."""
    if not name:
        return None
    with _lock:
        idx = _index
    if not idx:
        return None
    return idx.get(_normalize_key(name)) or idx.get(_alpha_key(name))


def load_llmstxt() -> dict[str, LlmsTxtEntry]:
    """Return current in-memory index (read-only snapshot)."""
    with _lock:
        return dict(_index)


def status() -> dict:
    """For /health and debugging."""
    with _lock:
        return {
            "entries": _n_entries,
            "index_keys": len(_index),
            "loaded": _loaded_at > 0,
        }
