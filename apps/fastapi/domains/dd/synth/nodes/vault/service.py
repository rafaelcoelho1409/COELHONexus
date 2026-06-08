"""I/O orchestrator for vault sentinelization.

Pure-function helpers (`sentinelize_doc`, `materialize`, `audit_roundtrip`,
`format_entry_for_prompt`, `format_entries_for_prompt`,
`score_entry_pedagogy`, `rank_hashes_by_pedagogy`, `build_manifest`,
`_hash_block`, `_make_sentinel`, `_parse_info_string`) live in
`.domain` and are re-exported via `__init__.py`. This module owns ONLY
the async MinIO read path that falls back to runtime sentinelization
for per-source vaults missing from the pre-built ingestion artifacts.

For the why-vaults-at-all rationale + audit dimensions, see `.domain`
+ `docs/SYNTH-ARCHITECTURE-SOTA-2026-05-18.md` (step 5).
"""
from __future__ import annotations

import json as _json

from .domain import sentinelize_doc
from .schemas import VaultEntry


# CRITICAL FIX (2026-05-24 evening) — read-time vault provisioning
# Root-cause discovery: ingestion produces per-page markdown files at
# `ingestion/{slug}/pages/{idx}-{slug}.md` but the vault builder only ran
# on the consolidated `llms-full.txt` crawl, producing exactly ONE
# `synth-vault/{slug}/pages/0000-gofastmcp-com-llms-full.vault.json` for
# all 335 fastmcp pages. When digest_construct calls extract_vault_hashes
# on individual ingestion pages they have NO sentinels → digest LLM emits
# empty code_refs → sawc has zero allowed_hashes per section → final
# chapter has zero code blocks.
#
# Surgical fix: lazy per-source sentinelization. When a per-source vault
# file doesn't exist on MinIO, run sentinelize_doc on the raw ingestion
# page at read time. This populates the runtime vault for downstream
# nodes WITHOUT requiring an ingestion-pipeline rebuild.


async def get_or_build_source_vault(
    minio, slug: str, source_key: str,
) -> tuple[str, dict[str, "VaultEntry"]]:
    """Return (sentinelized_text, vault_entries) for one source page.

    Resolution order:
      1. Pre-built per-source artifacts (`synth-vault/{slug}/pages/...
         {basename}.sentinelized.md` + `.vault.json`) — preferred path,
         used when the ingestion-time builder ran per-page.
      2. Runtime sentinelization of `ingestion/{slug}/pages/...` raw
         markdown — fallback when the per-page artifacts are missing
         (e.g., the consolidated `llms-full` crawl populated a single
         mega-vault instead of per-page vaults).

    Always returns sentinelized text so downstream nodes see
    `<code-ref hash="..."/>` placeholders in source bodies; the vault
    dict maps each hash to its VaultEntry (with the original fence_text
    body that render_audit_write materializes at the end of synth).
    """
    # Compute the expected per-page vault path. Mirror render's transform
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
