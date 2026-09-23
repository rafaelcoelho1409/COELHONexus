"""ycs/embedding_migration — consent-gated re-embed when the configured
embedding model changes.

2026-09-15: replaces `ingestion/service.py::ensure_collection`'s old
behavior of silently DROPPING the whole Qdrant collection the instant it
detected a stored-vs-configured `embedding_model` mismatch — that
destroyed every previously-ingested vector with no consent and no
recovery path other than a full manual Rerun. The new flow:

  1. `check_migration_needed()` — cheap, read-only: sample one point from
     the ACTIVE collection (alias-transparent), compare its stored
     `embedding_model` to what's currently configured.
  2. If mismatched, dispatch of new ingestion runs is BLOCKED (the gate
     lives in `api/v1/ycs/content/router.py`, checking `get_migration_state`)
     until the user explicitly starts a migration.
  3. `start_migration()` creates a NEW, versioned physical collection
     (old data untouched) and records migration state.
  4. The Celery task (`task.py`) re-embeds every ES-stored transcript into
     that new collection via the EXISTING `ingest_to_qdrant` (unmodified
     logic — chunking, cross-video packing, content-hash skip all apply
     the same way; the only difference is `collection_name` and that the
     new collection starts empty, so the hash-skip pre-pass naturally
     re-embeds everything).
  5. `cutover()` atomically re-points the `QDRANT_COLLECTION` alias at
     the new physical collection once the re-embed succeeds. The OLD
     physical collection is left in place (cheap, and a safety net) —
     nothing deletes it automatically.

Qdrant behavior this design relies on (verified live against the cluster,
2026-09-15): `GET /collections` never lists aliases, only real collection
names — `collection_exists()`/`get_collection()` DO resolve through an
alias transparently. `create_alias` re-points an alias that already
exists (no separate delete needed) but 409s if the alias NAME collides
with a REAL collection — so cutover only needs to pre-delete when
`QDRANT_COLLECTION` is still a real (pre-migration) collection."""
from __future__ import annotations
import domains
from . import domain, keys, params

import json
import logging
import time
from typing import Any

from qdrant_client import AsyncQdrantClient
from qdrant_client.http.models import CreateAlias, CreateAliasOperation


logger = logging.getLogger(__name__)


def _build_qdrant() -> AsyncQdrantClient:
    import os
    qdrant_api_key = os.environ.get("QDRANT_API_KEY")
    return AsyncQdrantClient(
        url     = os.environ.get("QDRANT_URL", "http://localhost:6333"),
        port    = int(os.environ.get("QDRANT_PORT", "6333")),
        api_key = qdrant_api_key if qdrant_api_key else None,
    )


async def check_migration_needed_now() -> dict[str, str] | None:
    """Convenience wrapper around `check_migration_needed` — builds its
    own short-lived Qdrant client and resolves the configured model, so
    a FastAPI router just needs ONE call to get a yes/no answer without
    managing a client itself.

    2026-09-15: this exists because a real gap was found — the Videos-
    tab dispatch endpoints (`api/v1/ycs/content`) had their own gate,
    but `api/v1/ycs/agents/router.py::ingest_to_qdrant` (the Source
    tab's "Continue to Qdrant" follow-up, live in the UI —
    `static/js/ycs/ingest.js`) dispatched the SAME bulk re-embed task
    with NO check at all, silently mixing a new video's vectors into
    the active collection under whatever model happened to be pinned
    at that moment, regardless of what every OTHER point in the
    collection was built with. Every entry point that can write to
    Qdrant needs to share this ONE check, not re-derive it per router."""
    qdrant = _build_qdrant()
    try:
        return await check_migration_needed(qdrant, domains.settings.embeddings.service.get_configured_model())
    finally:
        await qdrant.close()


async def get_active_collection_name(qdrant: AsyncQdrantClient) -> str:
    """The REAL physical collection `QDRANT_COLLECTION` currently
    resolves to — read-only, informational (Settings page "current
    collection" display). Returns the literal `QDRANT_COLLECTION` name
    itself when it isn't (yet) an alias — true before the first-ever
    migration, when it's still a plain collection."""
    try:
        resp = await qdrant.get_aliases()
        for a in resp.aliases:
            if a.alias_name == domains.ycs.ingestion.params.QDRANT_COLLECTION:
                return a.collection_name
    except Exception as e:
        logger.debug(f"[ycs:embedding_migration] alias lookup failed: {e}")
    return domains.ycs.ingestion.params.QDRANT_COLLECTION


async def check_migration_needed(qdrant: AsyncQdrantClient, configured_pinned_id: str) -> dict[str, str] | None:
    """None if nothing needs migrating (no active collection yet, its
    stored model already matches, or the user is in "auto" mode —
    explicitly opted OUT of the strict-consistency guarantee, so drift
    is expected/accepted there, not a gate condition). Otherwise
    `{"from_model", "to_model"}`.

    Read-only, cheap (one 1-point scroll) — safe to call on every
    dispatch attempt, not just periodically."""
    if (
        not configured_pinned_id
        or configured_pinned_id.strip().lower() == "auto"
        or not await qdrant.collection_exists(domains.ycs.ingestion.params.QDRANT_COLLECTION)
    ):
        return None
    try:
        points, _ = await qdrant.scroll(
            collection_name = domains.ycs.ingestion.params.QDRANT_COLLECTION,
            limit = 1,
            with_payload = ["embedding_model"],
            with_vectors = False,
        )
    except Exception as e:
        logger.warning(f"[ycs:embedding_migration] check failed: {type(e).__name__}: {e}")
        return None
    if not points:
        return None
    stored = (points[0].payload or {}).get("embedding_model")
    if not stored or stored == configured_pinned_id:
        return None
    return {"from_model": stored, "to_model": configured_pinned_id}


async def get_migration_state(redis: Any) -> dict[str, Any] | None:
    try:
        raw = await redis.get(keys.migration_state_key())
    except Exception:
        return None
    if not raw:
        return None
    try:
        return json.loads(raw)
    except Exception:
        return None


async def start_migration(
    redis: Any,
    qdrant: AsyncQdrantClient,
    *,
    from_model: str,
    to_model: str,
    dimensions: int,
) -> dict[str, Any]:
    """Creates the new physical collection + writes initial migration
    state (`task_id` empty — filled in by `dispatch_migration` once the
    Celery task actually exists, since the task itself needs the
    physical collection name as an argument, so it can't be dispatched
    before this runs).

    Idempotent-ish: if a migration is already running, returns the
    EXISTING state rather than starting a second one over it (the FastAPI
    trigger endpoint should still guard this with its own check, this is
    a belt-and-suspenders safeguard)."""
    existing = await get_migration_state(redis)
    if existing and existing.get("status") == "running":
        return existing
    physical = domain.physical_collection_name(domains.ycs.ingestion.params.QDRANT_COLLECTION, to_model, dimensions)
    # Fresh collection — `ensure_collection`'s model-mismatch guard can
    # never fire here (nothing stored yet to mismatch against).
    await domains.ycs.ingestion.service.ensure_collection(qdrant, dimensions, to_model, collection_name = physical)
    state = {
        "status":              "running",
        "from_model":          from_model,
        "to_model":            to_model,
        "physical_collection": physical,
        "task_id":             "",
        "started_at":          time.time(),
    }
    await redis.set(keys.migration_state_key(), json.dumps(state), ex = params.MIGRATION_STATE_TTL_S)
    logger.info(
        f"[ycs:embedding_migration] started: {from_model!r} -> {to_model!r} "
        f"(physical={physical!r})"
    )
    return state


async def set_migration_task_id(redis: Any, task_id: str) -> None:
    state = await get_migration_state(redis)
    if not state:
        return
    state["task_id"] = task_id
    await redis.set(keys.migration_state_key(), json.dumps(state), ex = params.MIGRATION_STATE_TTL_S)


async def dispatch_migration(
    redis: Any,
    qdrant: AsyncQdrantClient,
    *,
    from_model: str,
    to_model: str,
    dimensions: int,
) -> dict[str, Any]:
    """Full trigger: create the physical collection, dispatch the
    re-embed task (reusing `qdrant_task.task.ingest_to_qdrant` over ALL
    ES transcripts — `video_ids=None`), link the cutover finalize task to
    run on success. Returns the migration state including the real
    (pollable) task id.

    `.link()`, not `celery.chain()` — see `task.py`'s docstring for why:
    a chain's own `AsyncResult.id` refers to the LAST task, which stays
    PENDING for the whole (long) re-embed phase; `.link()` keeps the
    re-embed task's OWN id as what callers poll, so live progress is
    visible immediately."""
    state = await start_migration(
        redis, qdrant, from_model = from_model, to_model = to_model, dimensions = dimensions,
    )
    if state.get("task_id"):
        return state  # already dispatched (idempotency hit in start_migration)

    from domains.ycs.embedding_migration.task import finalize_embedding_migration
    from domains.ycs.qdrant_task.task import ingest_to_qdrant

    physical = state["physical_collection"]
    sig = ingest_to_qdrant.si(video_ids = None, collection_name = physical)
    sig.link(finalize_embedding_migration.si(physical))
    result = sig.apply_async()

    await set_migration_task_id(redis, result.id)
    state["task_id"] = result.id
    return state


async def cutover(redis: Any, qdrant: AsyncQdrantClient, physical_collection: str) -> None:
    """Atomically re-point the `QDRANT_COLLECTION` alias at the newly
    migrated physical collection, then clear migration state. Safe to
    call whether `QDRANT_COLLECTION` is currently a real (pre-migration)
    collection, an existing alias (a later migration), or absent
    entirely — see this module's docstring for the verified Qdrant
    behavior this relies on."""
    real_names = {c.name for c in (await qdrant.get_collections()).collections}
    if domains.ycs.ingestion.params.QDRANT_COLLECTION in real_names:
        await qdrant.delete_collection(domains.ycs.ingestion.params.QDRANT_COLLECTION)
    with domains.ycs.runtime.observability.spans.qdrant_admin_span(
        operation = "update_collection_aliases",
        collection = domains.ycs.ingestion.params.QDRANT_COLLECTION,
    ):
        await qdrant.update_collection_aliases(
            change_aliases_operations = [
                CreateAliasOperation(create_alias = CreateAlias(
                    collection_name = physical_collection, alias_name = domains.ycs.ingestion.params.QDRANT_COLLECTION,
                )),
            ],
        )
    await redis.delete(keys.migration_state_key())
    logger.info(
        f"[ycs:embedding_migration] cutover complete — {domains.ycs.ingestion.params.QDRANT_COLLECTION!r} "
        f"now aliases {physical_collection!r}"
    )
