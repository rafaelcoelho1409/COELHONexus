"""Helpers every synth node shares: progress events, content digests, the standard failure patch, tolerant JSON reads."""
from __future__ import annotations
import domains
from . import params

import hashlib
import json
import logging
import time
from typing import Any

from langgraph.config import get_stream_writer


logger = logging.getLogger(__name__)


def emit(step: str, kind: str, **fields: Any) -> None:
    """Send one progress event to the run's stream, in the shape the web reads (`step`, `kind`, ...fields).

    The run shell consumes the graph's `custom` stream and republishes each event on the run's channel. Outside a streamed run
    (a test that invokes the graph directly) there is no writer and the event is dropped."""
    try:
        writer = get_stream_writer()
    except RuntimeError:
        return
    writer({"step": step, "kind": kind, **fields})


def digest(*parts: str) -> str:
    """A short content digest of the parts, in order: the identity of a per-item result."""
    h = hashlib.sha256()
    for part in parts:
        h.update(part.encode("utf-8", "replace"))
        h.update(b"\x1f")
    return h.hexdigest()[:params.DIGEST_CHARS]


def elapsed_ms(since: float) -> int:
    return int((time.monotonic() - since) * 1000)


def failure(field: str, stats_field: str, reason: str, error: str, started: float, **extra: Any) -> dict:
    """The patch a node returns when it cannot run: an empty output, the reason in its stats, and the run marked failed."""
    return {
        field: "",
        stats_field: {"skipped": reason, "wall_ms": elapsed_ms(started), **extra},
        "status": "failed",
        "error": error,
    }


async def load_json(blobs: Any, key: str) -> dict | None:
    """A JSON blob as a dict, or None when it is missing or unreadable."""
    try:
        if not await blobs.exists(key):
            return None
        return json.loads(await blobs.read_text(key))
    except Exception:
        return None


async def write_blob(blobs: Any, key: str, payload: Any) -> str:
    body = json.dumps(payload, indent = 2, ensure_ascii = False)
    await blobs.write(key, body, content_type = "application/json")
    return body


async def load_vault_rich(blobs: Any, slug: str, source_keys: list[str]) -> tuple[dict[str, Any], int, int]:
    """`(vault, n_loaded, n_skipped)`: the `VaultEntry` of every code block of the chapter's sources (hash → entry), which is what lets the writer
    see actual code (lang, line count, body) while the render step substitutes byte-exact fences by hash. Pages without a pre-built vault are
    sentinelized in memory."""
    ingestion = domains.dd.ingestion
    entry_model = ingestion.vault.schemas.VaultEntry
    rich: dict[str, Any] = {}
    n_loaded = 0
    n_skipped = 0
    for source_key in source_keys:
        basename = source_key.rstrip("/").rsplit("/", 1)[-1].removesuffix(".md")
        run_id = ingestion.storage.keys.run_id_from_page_key(source_key)
        vault_key = f"{ingestion.storage.keys.vault_prefix(slug, run_id)}pages/{basename}.vault.json"
        used_runtime = False
        if await blobs.exists(vault_key):
            try:
                manifest = json.loads(await blobs.read_text(vault_key))
                for h, entry in ((manifest or {}).get("entries") or {}).items():
                    if not isinstance(entry, dict):
                        continue
                    try:
                        rich[h] = entry_model(**entry)
                    except Exception:
                        if entry.get("fence_text"):
                            rich[h] = entry_model(
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
                _, entries = ingestion.vault.domain.sentinelize_doc(raw)
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
