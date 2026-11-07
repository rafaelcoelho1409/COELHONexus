"""Readers and writers of the planner's MinIO artifacts: the node blobs that downstream stages read, and the per-item results of the fan-out nodes."""
from __future__ import annotations
import infra
from . import keys

import asyncio
import json
import logging
import time


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


async def write_planner_timing(minio, slug: str, total_wall_ms: int) -> None:
    """Best-effort write of the planner timing blob."""
    try:
        await minio.write(
            keys.planner_timing_key(slug),
            json.dumps({"slug": slug, "total_wall_ms": int(total_wall_ms), "finished_ts": time.time()}, indent = 2),
            content_type = JSON_TYPE,
        )
    except Exception as error:
        logger.warning(f"[planner] {slug}: timing persist failed ({type(error).__name__}: {error})")


async def load_json(minio, key: str) -> dict | None:
    """A JSON blob as a dict, or None when it is missing or unreadable."""
    try:
        if not await minio.exists(key):
            return None
        return json.loads(await minio.read_text(key))
    except Exception:
        return None


async def read_all(minio, object_keys: list[str]) -> list[str | None]:
    """The text of every key, in order; None for a key that could not be read.

    One bulk read first (a shared client, chunked and parallel); when it fails as a whole, key by key so one bad object costs one page."""
    if not object_keys:
        return []
    try:
        bodies = await minio.read_many(object_keys)
        if len(bodies) == len(object_keys):
            return list(bodies)
    except Exception as error:
        logger.warning(f"[planner] bulk read failed ({type(error).__name__}: {error}); reading key by key")

    async def one(key: str) -> str | None:
        try:
            return await minio.read_text(key)
        except Exception:
            return None

    return list(await asyncio.gather(*[one(key) for key in object_keys]))


async def read_plan_chapters(minio, slug: str) -> dict[str, dict]:
    """The current plan's chapters by id, for the stale-render check (fail-soft: without the plan no render is ever judged stale)."""
    try:
        plan = json.loads(await minio.read_text(keys.plan_latest_key(slug)))
        return {c["id"]: c for c in (plan.get("chapters") or []) if (c or {}).get("id")}
    except Exception as error:
        logger.info(f"[study-orchestrator] {slug}: plan unreadable for the stale-render check ({type(error).__name__})")
        return {}


async def read_study_chapter_ms(minio, slug: str) -> dict[str, int]:
    """The chapters' measured times from the prior timing blob, so chapters skipped on resume keep theirs."""
    try:
        prior = json.loads(await minio.read_text(keys.study_timing_key(slug)))
        return {str(k): int(v) for k, v in (prior.get("per_chapter_ms") or {}).items()}
    except Exception:
        return {}


async def write_study_timing(minio, slug: str, *, per_chapter_ms: dict[str, int], harmonize_ms: int, session_wall_ms: int, finished_ts: float) -> None:
    """Best-effort write of study timing; total = cumulative chapter wall + harmonize (resume-stable)."""
    payload = {
        "slug": slug,
        "total_wall_ms": sum(int(v) for v in per_chapter_ms.values()) + int(harmonize_ms),
        "per_chapter_ms": {k: int(v) for k, v in per_chapter_ms.items()},
        "harmonize_ms": int(harmonize_ms),
        "session_wall_ms": int(session_wall_ms),
        "finished_ts": finished_ts,
    }
    try:
        await minio.write(keys.study_timing_key(slug), json.dumps(payload, indent = 2), content_type = JSON_TYPE)
    except Exception as error:
        logger.warning(f"[study-orchestrator] {slug}: timing persist failed ({type(error).__name__}: {error})")


async def write_chapter_timing(minio, slug: str, chapter_id: str, wall_ms: int) -> None:
    """Best-effort merge of one finished chapter's wall time into the study timing blob (a single-chapter ▶ run leaves the other chapters' times, the harmonize
    time and the session fields as they were)."""
    key = keys.study_timing_key(slug)
    try:
        prior = json.loads(await minio.read_text(key))
    except Exception:
        prior = {}
    per_chapter_ms = {str(k): int(v) for k, v in (prior.get("per_chapter_ms") or {}).items()}
    per_chapter_ms[chapter_id] = int(wall_ms)
    harmonize_ms = int(prior.get("harmonize_ms") or 0)
    payload = {
        "slug": slug,
        "total_wall_ms": sum(per_chapter_ms.values()) + harmonize_ms,
        "per_chapter_ms": per_chapter_ms,
        "harmonize_ms": harmonize_ms,
        "session_wall_ms": int(prior.get("session_wall_ms") or wall_ms),
        "finished_ts": time.time(),
    }
    try:
        await minio.write(key, json.dumps(payload, indent = 2), content_type = JSON_TYPE)
    except Exception as error:
        logger.warning(f"[synth] {slug}/{chapter_id}: chapter timing persist failed ({type(error).__name__}: {error})")


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
