"""Async MinIO read path for vault sentinelization; falls back to runtime sentinelization when per-page vaults are missing from ingestion artifacts."""
from __future__ import annotations

import json as _json

from .domain import sentinelize_doc
from .schemas import VaultEntry


# CRITICAL: ingestion vault builder only ran on consolidated llms-full.txt (not per-page), so individual pages had no sentinels → digest emits empty code_refs → zero code blocks. Fix: lazy sentinelization at read time.


async def get_or_build_source_vault(
    minio, slug: str, source_key: str,
) -> tuple[str, dict[str, "VaultEntry"]]:
    """Return (sentinelized_text, vault_entries): tries pre-built per-source artifacts first, falls back to runtime sentinelize_doc when per-page artifacts are missing."""
    # (we can't import render here without creating a circular dep).
    basename = source_key.rstrip("/").rsplit("/", 1)[-1]
    if basename.endswith(".md"):
        basename = basename[:-3]
    vault_key = f"synth-vault/{slug}/pages/{basename}.vault.json"
    sentinel_key = f"synth-vault/{slug}/pages/{basename}.sentinelized.md"

    # 1. Try pre-built artifacts.
    if await minio.exists(vault_key) and await minio.exists(sentinel_key):
        try:
            manifest = _json.loads(await minio.read_text(vault_key))
            sentinelized = await minio.read_text(sentinel_key)
            entries: dict[str, VaultEntry] = {}
            for h, d in (manifest.get("entries") or {}).items():
                if isinstance(d, dict):
                    try:
                        entries[h] = VaultEntry(**d)
                    except Exception:
                        # Tolerate schema drift — fall back to a minimal
                        # entry so downstream gets the body at least.
                        if d.get("fence_text"):
                            entries[h] = VaultEntry(
                                hash = h,
                                fence_text = d.get("fence_text", ""),
                                info_string = d.get("info_string", ""),
                                lang = d.get("lang", ""),
                                line_count = int(d.get("line_count") or 0),
                                char_count = int(d.get("char_count") or 0),
                                sentinel_kind = d.get(
                                    "sentinel_kind", "fence_backtick",
                                ),
                            )
            return sentinelized, entries
        except Exception:
            # Fall through to runtime path if pre-built artifacts are
            # corrupted.
            pass

    # 2. Runtime sentinelization of the raw ingestion page.
    try:
        raw = await minio.read_text(source_key)
    except Exception:
        return "", {}
    if "<code-ref hash=" in raw:
        # Already sentinelized at source (shouldn't normally happen for
        # ingestion pages, but defensive).
        return raw, {}
    try:
        return sentinelize_doc(raw)
    except Exception:
        return raw, {}
