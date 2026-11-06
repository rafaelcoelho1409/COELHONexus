"""Readers and writers of the planner's MinIO artifacts: the node blobs that downstream stages read, and the per-item results of the fan-out nodes."""
from __future__ import annotations
import infra
from . import keys

import json
import logging


logger = logging.getLogger(__name__)

JSON_TYPE = "application/json"


async def load_plan(slug: str) -> dict | None:
    """The latest finalized plan blob (chapter outline + sources) for `slug`, or None when it does not exist yet.

    Raises when the blob exists but is unreadable or corrupt; the caller maps that onto its own error surface. The
    single reader of `plan-latest.json`: the planner writes it (plan_write), the synth router and outline node read it."""
    minio = infra.minio.service.get_storage()
    key = keys.plan_latest_key(slug)
    if not await minio.exists(key):
        return None
    return json.loads(await minio.read_text(key)) or {}


async def _load_blob(minio, key: str) -> dict:
    """A node blob as a dict, or {} when it is missing or unreadable."""
    try:
        return json.loads(await minio.read_text(key)) or {}
    except Exception:
        return {}


async def load_distillates(minio, slug: str) -> dict:
    """`{source_key: DocDistillate dict}` from the latest doc_distill blob; {} on a miss."""
    return (await _load_blob(minio, keys.distill_latest_key(slug))).get("distillates") or {}


async def load_proposals(minio, slug: str) -> dict:
    """The latest chapter_propose blob (`proposals`, `seeds`, ...); {} on a miss."""
    return await _load_blob(minio, keys.proposals_latest_key(slug))


async def load_assignments(minio, slug: str) -> dict:
    """`{source_key: [{chapter_idx, confidence}, ...]}` from the latest chapter_assign blob; {} on a miss."""
    return (await _load_blob(minio, keys.assignments_latest_key(slug))).get("assignments") or {}


async def write_blob(minio, versioned_key: str, latest_key: str, payload: dict) -> None:
    """A node's output twice: under its content hash (kept) and under the mutable latest pointer (what the next stage reads)."""
    body = json.dumps(payload, indent = 2, ensure_ascii = False)
    await minio.write(versioned_key, body, content_type = JSON_TYPE)
    await minio.write(latest_key, body, content_type = JSON_TYPE)


async def read_items(minio, slug: str, node: str, version: str, digests: list[str]) -> dict[str, dict]:
    """The per-item results already stored for `digests` (`{digest: result}`); items whose result is missing are simply absent.

    One listing finds the hits, one bulk read fetches them, so a fully cached 700-page corpus costs two round trips."""
    prefix = f"{keys.item_prefix(slug, node)}{version}/"
    stored = set(await minio.list(prefix))
    hits = [(digest, keys.item_key(slug, node, version, digest)) for digest in digests]
    hits = [(digest, key) for digest, key in hits if key in stored]
    if not hits:
        return {}
    bodies = await minio.read_many([key for _, key in hits])
    out: dict[str, dict] = {}
    for (digest, key), body in zip(hits, bodies):
        try:
            out[digest] = json.loads(body)
        except ValueError:
            logger.warning(f"[planner-items] unreadable result {key!r}; the item will be recomputed")
    return out


async def write_item(minio, slug: str, node: str, version: str, digest: str, result: dict) -> None:
    await minio.write(
        keys.item_key(slug, node, version, digest),
        json.dumps(result, ensure_ascii = False),
        content_type = JSON_TYPE,
    )


async def read_chapter_items(minio, slug: str, chapter_id: str, node: str, version: str, digests: list[str]) -> dict[str, dict]:
    """The per-item results a synth chapter already stored for `digests` (`{digest: result}`); same one-listing, one-bulk-read shape as `read_items`."""
    prefix = f"{keys.synth_item_prefix(slug, chapter_id, node)}{version}/"
    stored = set(await minio.list(prefix))
    hits = [(digest, keys.synth_item_key(slug, chapter_id, node, version, digest)) for digest in digests]
    hits = [(digest, key) for digest, key in hits if key in stored]
    if not hits:
        return {}
    bodies = await minio.read_many([key for _, key in hits])
    out: dict[str, dict] = {}
    for (digest, key), body in zip(hits, bodies):
        try:
            out[digest] = json.loads(body)
        except ValueError:
            logger.warning(f"[synth-items] unreadable result {key!r}; the item will be recomputed")
    return out


async def write_chapter_item(minio, slug: str, chapter_id: str, node: str, version: str, digest: str, result: dict) -> None:
    await minio.write(
        keys.synth_item_key(slug, chapter_id, node, version, digest),
        json.dumps(result, ensure_ascii = False),
        content_type = JSON_TYPE,
    )
