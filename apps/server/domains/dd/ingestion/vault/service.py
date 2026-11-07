"""Reading a chapter's code blocks back from the vaults the ingestion stored."""
from __future__ import annotations
import domains
from . import domain, schemas

import json
import logging
from typing import Any


logger = logging.getLogger(__name__)


async def load_rich(blobs: Any, slug: str, source_keys: list[str]) -> tuple[dict[str, Any], int, int]:
    """`(vault, n_loaded, n_skipped)`: the `VaultEntry` of every code block of the chapter's sources (hash → entry), which is what lets the writer
    see actual code (lang, line count, body) while the render step substitutes byte-exact fences by hash. Pages without a pre-built vault are
    sentinelized in memory."""
    rich: dict[str, Any] = {}
    n_loaded = 0
    n_skipped = 0
    for source_key in source_keys:
        basename = source_key.rstrip("/").rsplit("/", 1)[-1].removesuffix(".md")
        run_id = domains.dd.ingestion.storage.keys.run_id_from_page_key(source_key)
        vault_key = f"{domains.dd.ingestion.storage.keys.vault_prefix(slug, run_id)}pages/{basename}.vault.json"
        used_runtime = False
        if await blobs.exists(vault_key):
            try:
                manifest = json.loads(await blobs.read_text(vault_key))
                for h, entry in ((manifest or {}).get("entries") or {}).items():
                    if not isinstance(entry, dict):
                        continue
                    try:
                        rich[h] = schemas.VaultEntry(**entry)
                    except Exception:
                        if entry.get("fence_text"):
                            rich[h] = schemas.VaultEntry(
                                hash = h,
                                fence_text = entry.get("fence_text", ""),
                                info_string = entry.get("info_string", ""),
                                lang = entry.get("lang", ""),
                                line_count = int(entry.get("line_count") or 0),
                                char_count = int(entry.get("char_count") or 0),
                                sentinel_kind = entry.get("sentinel_kind", "fence_backtick"),
                            )
                n_loaded += 1
                continue
            except Exception as error:
                logger.warning(f"[synth] vault {vault_key!r} unreadable: {type(error).__name__}: {error} — falling back to runtime")
                used_runtime = True
        else:
            used_runtime = True
        if used_runtime:
            # Runtime fallback: read the raw ingestion page and sentinelize it on the fly.
            try:
                raw = await blobs.read_text(source_key)
                if not raw or "<code-ref hash=" in raw:
                    n_skipped += 1
                    continue
                _, entries = domain.sentinelize_doc(raw)
                if entries:
                    for h, entry in entries.items():
                        rich.setdefault(h, entry)
                    n_loaded += 1
                else:
                    n_skipped += 1
            except Exception as error:
                n_skipped += 1
                logger.warning(f"[synth] runtime-sentinelize failed for {source_key!r}: {type(error).__name__}: {error}")
    return rich, n_loaded, n_skipped
