"""ycs/graph_builder — async LLM → Neo4j entity-graph pipeline.

Imperative Shell (`docs/CODE-CONVENTIONS.md` §4): I/O + Cypher writes +
LLM dispatch. Pure decisions delegated to `domain.py`.
Public API:
  create_graph_transformer(llm) → LLMGraphTransformer
  extract_and_store_graph(transcripts, metadata_map, llm, neo4j_graph, batch_size)
  resolve_entities(neo4j_graph) → int (merged count)
  discover_schema(sample_transcripts, llm) → dict
  get_graph_stats(neo4j_graph) → dict
  build_video_metadata_graph(neo4j_graph, videos)
"""
from __future__ import annotations
import domains
from . import domain, params, prompts, schemas

import asyncio
import hashlib
import logging
import random
import time
import uuid
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator, Callable

from langchain_core.documents import Document
from langchain_experimental.graph_transformers import LLMGraphTransformer
from langchain_neo4j import Neo4jGraph
from rapidfuzz import fuzz


async def _embed_ids_for_resolution(ids: list[str]) -> dict[str, list[float]]:
    """Embed a batch of entity-id strings via the configured embedding
    endpoint, returning a `{id: vector}` map for downstream cosine
    comparisons.

    2026-09-13: previously pinned its own separate NVIDIAEmbeddings
    instance to `baai/bge-m3` specifically (tuned for short-string
    entity-id similarity — multilingual, clean cosine gap at 0.85).
    Now shares the SAME embedding-endpoint singleton as the main Qdrant
    path — the endpoint resolves its own model dynamically ("auto"), so
    there's no client-side way to pin a specific model per call anymore.
    `params.EMBED_COSINE_CUTOFF` (0.85) was tuned against bge-m3's score
    distribution specifically and may need re-tuning if the endpoint's
    current pick scores entity-id similarity differently — flagged, not
    silently assumed fine.

    Best-effort: any embedding-call failure logs a warning and returns
    `{}` — the caller falls back to fuzz-only behavior (drops the
    semantic gate but doesn't crash entity resolution). This degrades
    correctness silently (an endpoint outage could let through false
    merges), but preserves availability — same tradeoff as Steps 1+2's
    wide try/except guards."""
    if not ids:
        return {}
    try:
        vecs = await domains.ycs.embeddings.service.create_dense_embeddings().aembed_documents(ids)
    except Exception as e:
        logger.warning(
            f"[ycs:graph:resolve] embedding endpoint call failed; falling "
            f"back to fuzz-only merge for this label "
            f"({type(e).__name__}: {str(e)[:120]})"
        )
        return {}
    return {ids[i]: vecs[i] for i in range(min(len(ids), len(vecs)))}


logger = logging.getLogger(__name__)



def create_graph_transformer(llm: Any) -> LLMGraphTransformer:
    """Build the LLMGraphTransformer with `ignore_tool_usage=True`
    (June 2026 SOTA for cross-provider compatibility).

    Why this matters operationally — observed across 4+ providers
    during 2026-06-08 runs, all silently producing `0 nodes` from
    valid transcripts:

      groq/openai/gpt-oss-120b      BadRequestError: 'DynamicGraph':
                                    /properties/nodes/anyOf/0/items/
                                    required: `required` is required
      gemini/gemini-2.5-pro         GeminiException BadRequestError on
                                    nested anyOf in DynamicGraph
      nvidia_nim/qwen/qwen3.5-397b  HTTP 200 with `{nodes:[], rels:[]}`
      nvidia_nim/stepfun/step-3.5   HTTP 200 with `{nodes:[], rels:[]}`

    Same fundamental cause: LangChain's default path is
    `with_structured_output(method="function_calling")`, which fights
    each provider's function-calling schema validator. Groq + Gemini
    reject `anyOf` arms without `required`; NIM-hosted weaker models
    accept the schema but interpret the function-call wrapper as
    "respond with empty arrays".

    The maintainer-mentioned workaround (LangChain issues #26624,
    #27100): `ignore_tool_usage=True` switches to a plain-text
    prompt + `json_repair.loads()` parsing path. Works on any model
    that emits JSON in response to a prompt.

    Trade-offs:
      - Requires `json-repair` dep (added to pyproject.toml)
      - Drops `node_properties=True` / `relationship_properties=True`
        (incompatible with the unstructured path; we don't read those
        downstream anyway — graph_builder + resolver only use
        node.id + node.type + relationship.type)
      - Keeps `additional_instructions` (our prompts.EXTRACTION_INSTRUCTIONS)
        — the unstructured prompt still honors it via the system
        message append.

    With this change the YCS Neo4j bandit can fairly explore the full
    SYNTH_GROUP pool — `_YCS_NEO4J_ARM_BLOCKLIST` is no longer needed
    (kept as an empty frozenset for env-override emergencies)."""
    return LLMGraphTransformer(
        llm = llm,
        # `ignore_tool_usage=True` — switches to the unstructured
        # plain-text-prompt path. See docstring above for rationale.
        ignore_tool_usage = True,
        strict_mode = False,
        additional_instructions = prompts.EXTRACTION_INSTRUCTIONS,
    )


async def _acquire_extract_slot(
    redis: Any, key: str, limit: int, lease_s: float, poll_s: float = 0.5,
) -> str:
    """Distributed counting semaphore (Redis-sorted-set fair-semaphore
    pattern) — blocks until one of `limit` global slots is free, across
    EVERY worker process/pod sharing `redis`, not just this one.

    Each waiter registers `{token: now}` in the sorted set, then checks
    its own rank; rank < limit means it holds a slot. Stale holders
    (crashed worker, wedged call) self-heal via the `lease_s`-old
    score-eviction sweep run on every attempt — no separate reaper
    process needed. Returns `token`; caller MUST release it via
    `_release_extract_slot` (a `finally` block) or the slot leaks until
    `lease_s` expires."""
    token = uuid.uuid4().hex
    while True:
        now = time.time()
        await redis.zremrangebyscore(key, "-inf", now - lease_s)
        await redis.zadd(key, {token: now})
        rank = await redis.zrank(key, token)
        if rank is not None and rank < limit:
            return token
        await redis.zrem(key, token)
        await asyncio.sleep(poll_s + random.uniform(0, poll_s))


async def _release_extract_slot(redis: Any, key: str, token: str) -> None:
    try:
        await redis.zrem(key, token)
    except Exception:
        pass


@asynccontextmanager
async def _distributed_extract_slot(
    redis: Any, key: str, limit: int, lease_s: float,
) -> AsyncIterator[None]:
    token = await _acquire_extract_slot(redis, key, limit, lease_s)
    try:
        yield
    finally:
        await _release_extract_slot(redis, key, token)


async def extract_and_store_graph(
    transcripts: list[dict],
    metadata_map: dict,
    llm: Any,
    neo4j_graph: Neo4jGraph,
    batch_size: int = params.DEFAULT_BATCH_SIZE,
    progress_cb: Callable[[dict[str, Any]], None] | None = None,
    run_resolution: bool = True,
    extract_id: str | None = None,
) -> dict:
    """One LLM call PER TRANSCRIPT (not per chunk). Deprecated rationale:
    full context → +30% entity quality vs chunked, and 352 calls instead
    of 2911 for a 352-video corpus.

    streaming concurrency + per-video silent-zero
    gate (replaces the barrier-batch loop):

      - `batch_size` > 1 is the pool width (back-compat: the agents
        endpoint documents it as "concurrent LLM calls"); `<= 1` means
        "use params.EXTRACT_CONCURRENCY" — the old `=1` callers wanted
        per-video PROGRESS granularity, which the streaming pool now
        provides at any width, so sequential execution is no longer the
        price of a granular progress bar.
      - A semaphore keeps N single-transcript extractions in flight;
        results are consumed in COMPLETION order (one ~180 s reasoning
        arm response doesn't stall the other in-flight videos, and the
        2 s/batch `time.sleep` barrier is gone).
      - PER-VIDEO silent-zero gate: an extraction that returns 0 nodes
        AND 0 rels does NOT get its source Document written to Neo4j.
        Before, `include_source=True` stamped the video_id tag even for
        empty extractions, so an intermittently-zeroing arm PERMANENTLY
        marked those videos done — the re-run skip check then hid the
        loss forever. Now they land in `failed_ids`, stay untagged, and
        get retried on the next segment/run.

    Idempotent — skips any video whose `video_id` is already tagged on
    a Document node in Neo4j (and only PRODUCTIVE videos get tagged).

    2026-09-13: dropped the `abort_after_consecutive` circuit breaker.
    It existed so the caller could "swap to a different arm" after 3
    consecutive failures — but every swap was confirmed to resolve to
    the same target regardless of any client-side exclusion set.
    "Swapping arms" therefore replayed the identical
    call — the abort bought nothing but abandoning whatever else was
    still in-flight in the pool. Now every document in `documents` gets
    a real attempt; the caller (`neo4j_task`) retries only the videos
    that come back in `failed_video_ids`, not the whole batch.

    Returns counters dict suitable for the API response envelope."""
    # LLM-usage drawer (mirrors DD Planner/Synth's) — attached here,
    # not inside create_graph_transformer, so that utility stays a
    # plain "given an llm, build a transformer" function. Only fires
    # when `extract_id` is supplied (the agents-endpoint direct callers
    # that predate this feature pass none, and stay silently unmetered
    # rather than erroring).
    if extract_id:
        llm = llm.with_config(
            callbacks=[domains.ycs.runtime.llm_counter.service.YCSLLMUsageCallback()],
        )
    transformer = create_graph_transformer(llm)
    concurrency = (
        batch_size if batch_size and batch_size > 1 else params.EXTRACT_CONCURRENCY
    )
    # Distributed slot — see _acquire_extract_slot's docstring. Closed
    # in the `finally:` below alongside the in-flight-task cleanup;
    # unused (never acquired) when `documents` ends up empty.
    redis_client = domains.ycs.pipeline_task.service.build_redis_client()
    total_nodes = 0
    total_relationships = 0
    total_processed = 0
    total_skipped = 0

    # Skip-on-re-run, fingerprint-aware (2026-09-14, DD manifest_hash
    # pattern adapted): a video skips only when its Document carries a
    # matching `transcript_sha` AND `extract_prompt_version`. A
    # transcript edit or a prompt bump (params.EXTRACT_PROMPT_VERSION) makes
    # the tag stale → re-extract instead of trusting outdated entities.
    # Stale Documents are wiped first (scoped delete) so the re-extract
    # can't duplicate nodes.
    current_fingerprints: dict[str, tuple[str, int]] = {}
    for transcript in transcripts:
        if not isinstance(transcript, dict):
            continue
        vid = transcript.get("video_id", "")
        content = transcript.get("content") or ""
        if vid and content.strip():
            current_fingerprints[vid] = (
                hashlib.sha256(content.encode("utf-8")).hexdigest()[:16],
                params.EXTRACT_PROMPT_VERSION,
            )
    already_processed: set[str] = set()
    stale_ids: list[str] = []
    try:
        result = neo4j_graph.query(
            f"MATCH (d:Document:{params.SOURCE_LABEL}) WHERE d.video_id IS NOT NULL "
            "RETURN d.video_id AS vid, d.transcript_sha AS sha, "
            "       d.extract_prompt_version AS ver"
        )
        for row in result or []:
            if not isinstance(row, dict):
                continue
            vid = row.get("vid")
            if not vid or vid not in current_fingerprints:
                continue
            if ((row.get("sha"), row.get("ver"))
                    == current_fingerprints[vid]):
                already_processed.add(vid)
            else:
                stale_ids.append(vid)
        if already_processed:
            logger.info(
                f"[ycs:graph] {len(already_processed)} videos already in "
                f"Neo4j with current fingerprint; skip"
            )
        if stale_ids:
            logger.info(
                f"[ycs:graph] {len(stale_ids)} video(s) with stale "
                f"fingerprint — wiping for re-extract: {stale_ids[:10]}"
            )
            delete_documents_for_videos(neo4j_graph, stale_ids)
    except Exception:
        pass

    # support 128K tokens — no truncation).
    documents: list[Document] = []
    for transcript in transcripts:
        if not isinstance(transcript, dict):
            continue
        vid = transcript.get("video_id", "")
        if not vid or vid in already_processed:
            total_skipped += 1
            continue
        content = transcript.get("content") or ""
        if not content.strip():
            continue
        meta = metadata_map.get(vid, {})
        if not isinstance(meta, dict):
            meta = {}
        sha, ver = current_fingerprints.get(vid, ("", params.EXTRACT_PROMPT_VERSION))
        doc_metadata: dict[str, Any] = {
            "video_id": vid,
            "title":    meta.get("title", ""),
            "channel":  meta.get("channel", ""),
            # Fingerprint — written onto the Document node via
            # include_source (`SET d += metadata`), read back by
            # the skip check above on re-runs.
            "transcript_sha":         sha,
            "extract_prompt_version": ver,
        }
        # 2026-09-14: long-video partitioning — carried onto the
        # Document node itself (not just ES) so admin listing / any
        # future graph query can find "every partition of video X"
        # without needing to parse the id string. Absent for the
        # overwhelming majority of (unsplit) videos.
        parent_vid = transcript.get("parent_video_id")
        if parent_vid:
            doc_metadata["parent_video_id"] = parent_vid
            doc_metadata["part_index"] = transcript.get("part_index")
            doc_metadata["part_total"] = transcript.get("part_total")
        documents.append(
            Document(page_content = content, metadata = doc_metadata),
        )

    logger.info(
        f"[ycs:graph] processing {len(documents)} transcripts "
        f"(skipped {total_skipped}, concurrency={concurrency})"
    )

    # Per-video status tracking for the Ingest-page right-column list.
    # The streaming pool completes one video at a time, so
    # completed_ids / failed_ids advance per video, matching Phase 1
    # and Phase 2's granularity. `current_batch`/`total_batches` keep
    # their keys for JS compat — batch ≡ video now.
    completed_ids: list[str] = []
    failed_ids:    list[str] = []
    if progress_cb:
        progress_cb({
            "phase":         "extracting",
            "current":       0,
            "total":         len(documents),
            "current_batch": 0,
            "total_batches": len(documents),
            "nodes":         0,
            "rels":          0,
            "completed_ids": list(completed_ids),
            "failed_ids":    list(failed_ids),
        })

    # Track the LAST per-video error so the silent-zero guard downstream
    # can surface the actual LLM error body in the log (otherwise the
    # user only sees "0 nodes" with no diagnostic). `error_breakdown`
    # counts per failure KIND (DD's histogram pattern) so a 25-video
    # run with 3 different failure modes stays diagnosable.
    last_batch_error: str | None = None
    error_breakdown: dict[str, int] = {}
    thin_video_ids: list[str] = []

    def _record_error(err: str | None) -> None:
        if not err:
            return
        kind = err.split(":")[0].strip() or "unknown"
        error_breakdown[kind] = error_breakdown.get(kind, 0) + 1

    def _is_infra_error(err: str) -> bool:
        # Shared definition in domain.py (also used by neo4j_task's
        # streak halt) — kept as a thin alias so call sites read local.
        return domain.is_infra_error(err)

    def _is_overflow_error(err: str) -> bool:
        return domain.is_overflow_error(err)

    def _split_content(content: str) -> list[str]:
        """Halve a too-large transcript on paragraph boundaries (DD's
        shrink-and-retry, adapted: split instead of truncate — truncating
        would silently drop entities)."""
        paras = [p for p in content.split("\n\n") if p.strip()]
        if len(paras) < 4:
            mid = len(content) // 2
            return [content[:mid], content[mid:]]
        mid = len(paras) // 2
        return ["\n\n".join(paras[:mid]), "\n\n".join(paras[mid:])]

    # In-process pool width (this task's own view) — kept alongside the
    # distributed slot below since a single task can still be asked for
    # batch_size > 1 (the agents endpoint's direct callers).
    sem = asyncio.Semaphore(concurrency)

    async def _convert(doc: Document):
        async with sem:
            # 2026-09-14: the GLOBAL slot's limit must be the configured
            # params.EXTRACT_CONCURRENCY, NOT the local `concurrency` above —
            # that one is batch_size-derived and varies per chunk (a
            # 2-video leftover chunk computes concurrency=2). Passing
            # it here meant two chunks running at once would enforce
            # TWO DIFFERENT ceilings on the SAME shared Redis semaphore
            # — whichever chunk was smaller silently capped the whole
            # run's true concurrency to its own size, independent of
            # what params.EXTRACT_CONCURRENCY was actually configured to
            # (observed live: a 2-video leftover chunk capped the run
            # to 2 concurrent slots even with params.EXTRACT_CONCURRENCY=3).
            async with _distributed_extract_slot(
                redis_client, params.NEO4J_EXTRACT_SEM_KEY, params.EXTRACT_CONCURRENCY,
                params.NEO4J_EXTRACT_SEM_LEASE_S,
            ):
                return await asyncio.wait_for(
                    transformer.aconvert_to_graph_documents([doc]),
                    timeout = params.GRAPH_BATCH_TIMEOUT_S,
                )

    async def _extract_one(doc: Document) -> tuple[str, Any, str | None]:
        """One transcript → (video_id, GraphDocument|None, error|None).
        Exceptions are mapped to the error string here so the consumer
        loop keeps video attribution in completion order."""
        vid = doc.metadata.get("video_id", "") if isinstance(doc.metadata, dict) else ""
        if extract_id:
            domains.ycs.runtime.llm_counter.service.set_context(
                extract_id = extract_id, video_id = vid,
            )
        try:
            # Watchdog: hard wall-clock ceiling per transcript. The
            # inner request stack already has per-deployment timeouts +
            # a zero-timeout-retry policy (see _build_pinned_chain), so
            # this only fires when that stack wedges — and guarantees
            # one slow arm can't burn the whole run before the bandit
            # gets its negative reward. Gating (both in-process AND
            # distributed) lives inside `_convert` now.
            gdocs = await _convert(doc)
            if not gdocs:
                return vid, None, None
            # Overflow split-union (2026-09-14): a context-overflow
            # manifests as an exception, not an empty result — but
            # if the FIRST segment errors with overflow markers on
            # a large doc, retrying the same full doc is doomed.
            # Handled in the except branch below via `_split_content`
            # (needs the original doc — kept in scope here).
            return vid, (gdocs[0] if gdocs else None), None
        except asyncio.CancelledError:
            raise
        except Exception as e:
            if isinstance(e, TimeoutError) and not str(e):
                # asyncio.wait_for raises a bare TimeoutError — stamp it
                # so the silent-zero guard's diagnostic isn't empty.
                err = (
                    f"TimeoutError: extraction exceeded the "
                    f"{params.GRAPH_BATCH_TIMEOUT_S:.0f}s watchdog"
                )
            else:
                err = f"{type(e).__name__}: {str(e)[:400]}"
            # Overflow → split in halves and extract per segment,
            # unioning nodes/rels (truncate would drop entities).
            if _is_overflow_error(err):
                try:
                    parts = _split_content(doc.page_content or "")
                    merged_nodes: list = []
                    merged_rels: list = []
                    first = None
                    for part in parts:
                        seg = Document(
                            page_content = part,
                            metadata = dict(doc.metadata),
                        )
                        seg_gdocs = await _convert(seg)
                        if seg_gdocs and seg_gdocs[0]:
                            if first is None:
                                first = seg_gdocs[0]
                            merged_nodes.extend(seg_gdocs[0].nodes or [])
                            merged_rels.extend(
                                seg_gdocs[0].relationships or []
                            )
                    if first is not None and (merged_nodes or merged_rels):
                        first.nodes = merged_nodes
                        first.relationships = merged_rels
                        return vid, first, None
                    err = f"{err} (split-union yielded nothing)"
                except Exception as split_e:
                    err = f"{err} | split-union failed: {type(split_e).__name__}"
            # 2026-09-14: non-transient errors (anything that ISN'T a
            # timeout/connection/rate-limit/5xx from the provider) are
            # almost certainly code bugs (e.g. the observed
            # `AttributeError: 'list' object has no attribute 'get'`
            # from inside the transformer) — log the full traceback
            # once here so the next occurrence is diagnosable instead
            # of just a one-line `failed:` warning downstream.
            if not _is_infra_error(err):
                logger.exception(f"[ycs:graph] {vid} unexpected error")
            return vid, None, err

    pool = [asyncio.create_task(_extract_one(doc)) for doc in documents]
    try:
        for fut in asyncio.as_completed(pool):
            vid, gdoc, err = await fut
            total_processed += 1
            if err is not None:
                last_batch_error = err
                _record_error(err)
                logger.warning(
                    f"[ycs:graph] {vid} failed: {err}. Continuing."
                )
                if vid and vid not in failed_ids:
                    failed_ids.append(vid)
            elif gdoc is None or (not gdoc.nodes and not gdoc.relationships):
                # PER-VIDEO silent zero — clean LLM response with no
                # entities. Do NOT write the source Document: tagging it
                # would permanently mark this video done and the re-run
                # entity-dense; an empty result is a model failure, not
                # a property of the video.
                last_batch_error = (
                    f"silent-zero: model returned no entities for {vid}"
                )
                _record_error(last_batch_error)
                logger.warning(
                    f"[ycs:graph] {vid}: clean response but 0 entities — "
                    f"left untagged for retry"
                )
                if vid and vid not in failed_ids:
                    failed_ids.append(vid)
            else:
                # LLMGraphTransformer occasionally emits id as a StringArray; coerce before writing to Neo4j.
                # `type` is sanitized too (see sanitize_neo4j_label's
                # docstring) — it's written verbatim as a Neo4j label/
                # relationship-type token by langchain-neo4j's own
                # unsanitized Cypher; an unprintable/empty value there
                # previously poisoned the ENTIRE video's write.
                clean_nodes = []
                for node in gdoc.nodes:
                    node.id = domain.coerce_entity_id(node.id)
                    node.type = domain.sanitize_neo4j_label(node.type)
                    if node.id:
                        clean_nodes.append(node)
                gdoc.nodes = clean_nodes
                for rel in (gdoc.relationships or []):
                    rel.type = domain.sanitize_neo4j_label(
                        rel.type, fallback = "RELATED_TO",
                    )
                # Quality gates (diagnostic only — never fail a video
                # here; thin-but-real graphs must still land):
                # orphan rels reference node ids absent from this
                # video's node set; thin = suspiciously few entities.
                _node_ids = {n.id for n in gdoc.nodes if n.id}
                _orphans = sum(
                    1 for r in (gdoc.relationships or [])
                    if getattr(getattr(r, "source", None), "id", None) not in _node_ids
                    or getattr(getattr(r, "target", None), "id", None) not in _node_ids
                )
                if _orphans:
                    logger.warning(
                        f"[ycs:graph] {vid}: {_orphans} orphan "
                        f"relationship(s) dropped from write "
                        f"({len(gdoc.nodes)} nodes)"
                    )
                    gdoc.relationships = [
                        r for r in (gdoc.relationships or [])
                        if getattr(getattr(r, "source", None), "id", None) in _node_ids
                        and getattr(getattr(r, "target", None), "id", None) in _node_ids
                    ]
                if 0 < len(gdoc.nodes) < 3:
                    thin_video_ids.append(vid)
                    logger.warning(
                        f"[ycs:graph] {vid}: thin graph "
                        f"({len(gdoc.nodes)} nodes, "
                        f"{len(gdoc.relationships or [])} rels) — kept"
                    )
                # video_id tagging happens NATIVELY inside
                # add_graph_documents: langchain-neo4j's include_source
                # path runs `SET d += $document.metadata`, and our
                # source Documents carry {video_id, title, channel}.
                # 2026-09-14: sync driver call in a thread + watchdog
                # (DD's bounded-write pattern) — a wedged Bolt
                # connection must not hang the task past its budget.
                try:
                    await asyncio.wait_for(
                        asyncio.to_thread(
                            neo4j_graph.add_graph_documents,
                            [gdoc],
                            include_source = True,
                            baseEntityLabel = True,
                        ),
                        timeout = params.WRITE_TIMEOUT_S,
                    )
                except Exception as write_err:
                    # 2026-09-14: widened 200->500 — the 200-char cut
                    # previously truncated Neo4j's ClientError body
                    # right before the kernel's specific IllegalToken-
                    # NameException reason, making a real write failure
                    # undiagnosable from logs alone.
                    _werr = f"{type(write_err).__name__}: {str(write_err)[:500]}"
                    last_batch_error = _werr
                    _record_error(_werr)
                    logger.warning(
                        f"[ycs:graph] {vid} write failed: {_werr}. Continuing."
                    )
                    if vid and vid not in failed_ids:
                        failed_ids.append(vid)
                    # Progress emission below still runs (counts the
                    # attempt); skip the success accounting.
                    if progress_cb:
                        meta = metadata_map.get(vid, {}) if vid else {}
                        if not isinstance(meta, dict):
                            meta = {}
                        progress_cb({
                            "phase":         "extracting",
                            "current":       total_processed,
                            "total":         len(documents),
                            "current_batch": total_processed,
                            "total_batches": len(documents),
                            "nodes":         total_nodes,
                            "rels":          total_relationships,
                            "completed_ids": list(completed_ids),
                            "failed_ids":    list(failed_ids),
                            "current_item": {
                                "id":      vid,
                                "title":   meta.get("title", ""),
                                "channel": meta.get("channel", ""),
                            } if vid else None,
                        })
                    continue
                # Best-effort source tagging — does not affect
                # success/failure accounting (the graph data itself
                # already landed correctly above; a missed tag is a
                # scoping gap for resolve_entities, not data loss).
                # Scoped by video_id: Document's real MERGE key is a
                # content-hash LangChain auto-derives when metadata
                # carries no "id" — video_id is the reliable property
                # already used elsewhere in this file for this exact
                # video-scoped-query purpose. See params.py's
                # params.PROJECT_LABEL/params.SOURCE_LABEL comment for why.
                try:
                    await asyncio.wait_for(
                        asyncio.to_thread(
                            neo4j_graph.query,
                            f"MATCH (d:Document {{video_id: $video_id}}) "
                            f"SET d:{params.PROJECT_LABEL}:{params.SOURCE_LABEL} "
                            "WITH d "
                            "MATCH (d)-[:MENTIONS]->(e:__Entity__) "
                            f"SET e:{params.PROJECT_LABEL}:{params.SOURCE_LABEL}",
                            params = {"video_id": vid},
                        ),
                        timeout = params.WRITE_TIMEOUT_S,
                    )
                except Exception as tag_err:
                    logger.warning(
                        f"[ycs:graph] {vid} source-tagging failed "
                        f"(graph write itself succeeded): "
                        f"{type(tag_err).__name__}: {str(tag_err)[:200]}"
                    )
                total_nodes += len(gdoc.nodes)
                total_relationships += len(gdoc.relationships or [])
                if vid and vid not in completed_ids:
                    completed_ids.append(vid)
                logger.info(
                    f"[ycs:graph] {vid}: "
                    f"{total_processed}/{len(documents)} transcripts, "
                    f"{total_nodes} nodes, {total_relationships} rels"
                )
            # Per-completion progress emission so the FastHTML Neo4j bar
            # advances in real time. `current` counts attempted (not
            # just succeeded) transcripts so the bar fills monotonically
            # even when an individual extraction raises.
            if progress_cb:
                meta = metadata_map.get(vid, {}) if vid else {}
                if not isinstance(meta, dict):
                    meta = {}
                progress_cb({
                    "phase":         "extracting",
                    "current":       total_processed,
                    "total":         len(documents),
                    "current_batch": total_processed,
                    "total_batches": len(documents),
                    "nodes":         total_nodes,
                    "rels":          total_relationships,
                    "completed_ids": list(completed_ids),
                    "failed_ids":    list(failed_ids),
                    "current_item": {
                        "id":      vid,
                        "title":   meta.get("title", ""),
                        "channel": meta.get("channel", ""),
                    } if vid else None,
                })
    finally:
        # in-flight videos stay untagged and retry on the next segment.
        for t in pool:
            if not t.done():
                t.cancel()
        await asyncio.gather(*pool, return_exceptions = True)
        try:
            await redis_client.close()
        except Exception:
            pass

    # Entity resolution is a GLOBAL pass over Neo4j — callers that loop
    # retry passes (neo4j_task's retry-failed-only loop) pass
    # `run_resolution=False` and run it ONCE after the last pass;
    # standalone callers keep the default.
    resolved = 0
    if run_resolution:
        if progress_cb:
            progress_cb({
                "phase":   "resolving",
                "current": len(documents),
                "total":   len(documents),
                "nodes":   total_nodes,
                "rels":    total_relationships,
            })
        logger.info("[ycs:graph] entity resolution starting")
        resolved = await resolve_entities(neo4j_graph)
        logger.info(f"[ycs:graph] entity resolution: {resolved} nodes merged")

    return {
        "documents_processed":   total_processed,
        "nodes_created":         total_nodes,
        "relationships_created": total_relationships,
        "entities_merged":       resolved,
        # Per-video outcome counts — `videos_failed > 0`
        # drives neo4j_task's residual-retry loop: failed videos are
        # untagged, so calling this function again (same transcripts,
        # different arm) retries exactly them.
        "videos_completed":      len(completed_ids),
        "completed_video_ids":   list(completed_ids),
        "videos_failed":         len(failed_ids),
        "failed_video_ids":      list(failed_ids),
        # Surface the most-recent per-video LLM exception so the
        # neo4j_task's silent-zero guard can log the body (otherwise
        # the user only sees "0 nodes" with no diagnostic). None when
        # every extraction succeeded. `error_breakdown` histograms
        # failure KINDS across the batch (DD pattern); `thin_video_ids`
        # flags suspiciously-small-but-kept graphs for tuning.
        "last_batch_error":      last_batch_error,
        "error_breakdown":       dict(error_breakdown),
        "thin_video_ids":        list(thin_video_ids),
    }



async def resolve_entities(neo4j_graph: Neo4jGraph) -> int:
    """Three-pass deduplication of `__Entity__` nodes:

      1. Lowercase + trim every id.
      2. Cypher MERGE exact duplicates per `(label, id)`.
      3. rapidfuzz fuzzy merge at `params.FUZZ_MERGE_CUTOFF` (75) per label,
         skipping NUMERIC_LABELS_SKIP where lexical similarity ≠ semantic
         identity.

    Returns the count of nodes merged. Best-effort: per-step failures
    are logged and skipped — the graph stays usable even if APOC isn't
    installed."""
    merged_count = 0

    # Heal historical list-typed ids (LLMGraphTransformer used to emit them as lists).
    # `apoc.refactor.mergeNodes(... properties: 'combine')` USED to
    # concatenate conflicting property values into lists, corrupting
    # `e.id` into `['brasil', 'brazil']` shapes that broke every
    # downstream Cypher touching it AND hid cross-channel bridges
    # (the two singletons that should have unified were instead
    # trapped inside the list — `['Brasil','Brazil']` was a single
    # broken node mentioned by 5 videos across 2 channels rather than
    # a Brasil-Brazil bridge). Step 2/3 now use `properties: 'discard'`
    # so NEW merges keep `n1`'s canonical scalar; this step heals any
    # EXISTING list-typed id by:
    #   (a) trying each element of the list as a potential scalar
    #       MENTIONS that landed on the list-node);
    #   (b) if no twin exists, fall back to SET id = first element.
    # A naive SET would have failed the uniqueness constraint (every
    # broken list had a scalar twin sitting alongside it — that's how
    # the corruption was created in the first place). Safe to re-run.
    try:
        rows = neo4j_graph.query(
            f"MATCH (n:__Entity__:{params.SOURCE_LABEL}) "
            "WHERE valueType(n.id) CONTAINS 'LIST' "
            "RETURN elementId(n) AS nid, n.id AS raw, "
            "       [l IN labels(n) WHERE l <> '__Entity__'] AS lbls"
        )
        n_merged = 0
        n_set = 0
        n_skip = 0
        for row in rows:
            nid = row["nid"]
            raw = row.get("raw")
            lbls = row.get("lbls") or []
            candidates = (
                [str(x) for x in raw if isinstance(x, str) and x.strip()]
                if isinstance(raw, (list, tuple)) else []
            )
            if not candidates:
                n_skip += 1
                continue
            # (a) try each candidate as a merge target
            merged_into = None
            for cand in candidates:
                try:
                    res = neo4j_graph.query(
                        f"MATCH (broken:__Entity__:{params.SOURCE_LABEL}) "
                        "WHERE elementId(broken) = $nid "
                        f"MATCH (twin:__Entity__:{params.SOURCE_LABEL}) "
                        "WHERE twin <> broken AND twin.id = $cand "
                        "AND any(L IN labels(twin) "
                        "        WHERE L IN $lbls AND L <> '__Entity__') "
                        "AND NOT valueType(twin.id) CONTAINS 'LIST' "
                        "WITH broken, twin LIMIT 1 "
                        "CALL apoc.refactor.mergeNodes([twin, broken], "
                        "  {properties: 'discard', mergeRels: true}) "
                        "YIELD node RETURN node.id AS kept",
                        params = {"nid": nid, "cand": cand, "lbls": lbls},
                    )
                    if res:
                        merged_into = cand
                        break
                except Exception:
                    continue
            if merged_into is not None:
                n_merged += 1
                logger.info(
                    f"[ycs:graph:resolve] heal-merge {raw} → "
                    f"{merged_into!r} (twin found)"
                )
                continue
            # (b) no twin — SET to first candidate (becomes a fresh scalar)
            try:
                neo4j_graph.query(
                    "MATCH (n) WHERE elementId(n) = $nid SET n.id = $s",
                    params = {"nid": nid, "s": candidates[0]},
                )
                n_set += 1
                logger.info(
                    f"[ycs:graph:resolve] heal-set {raw} → "
                    f"{candidates[0]!r} (no twin)"
                )
            except Exception as e:
                n_skip += 1
                logger.warning(
                    f"[ycs:graph:resolve] heal-set failed for "
                    f"elementId={nid}: {type(e).__name__}: {e}"
                )
        if n_merged or n_set or n_skip:
            logger.info(
                f"[ycs:graph:resolve] list-typed id heal: "
                f"merged={n_merged} set={n_set} skipped={n_skip}"
            )
    except Exception as e:
        logger.warning(
            f"[ycs:graph:resolve] list-typed id heal failed: {e}"
        )

    # Python-side normalize (vs Cypher trim()) — trim() blows up on StringArray ids from LLMGraphTransformer.
    try:
        rows = neo4j_graph.query(
            f"MATCH (n:__Entity__:{params.SOURCE_LABEL}) WHERE n.id IS NOT NULL "
            "RETURN elementId(n) AS nid, n.id AS raw_id"
        )
        updates = []
        for row in rows:
            nid = row.get("nid")
            raw = row.get("raw_id")
            canonical = domain.normalize_entity_id(raw)
            if not canonical:
                continue
            if canonical != raw:
                updates.append({"nid": nid, "new_id": canonical})
        if updates:
            neo4j_graph.query(
                "UNWIND $updates AS u "
                "MATCH (n) WHERE elementId(n) = u.nid "
                "SET n.id = u.new_id",
                params = {"updates": updates},
            )
        logger.info(
            f"[ycs:graph:resolve] normalized {len(updates)} ids "
            f"(scanned {len(rows)})"
        )
    except Exception as e:
        logger.warning(f"[ycs:graph:resolve] normalize failed: {e}")

    # Exact merge (same label + same normalized id).
    try:
        result = neo4j_graph.query(
            f"MATCH (n1:__Entity__:{params.SOURCE_LABEL}), (n2:__Entity__:{params.SOURCE_LABEL}) "
            "WHERE n1 <> n2 AND n1.id = n2.id "
            "AND any(label IN labels(n1) WHERE label IN labels(n2) AND label <> '__Entity__') "
            "WITH n1, collect(DISTINCT n2) AS duplicates "
            "WHERE size(duplicates) > 0 "
            "CALL apoc.refactor.mergeNodes([n1] + duplicates, "
            "  {properties: 'discard', mergeRels: true}) YIELD node "
            "RETURN count(node) AS merged"
        )
        merged_count = result[0]["merged"] if result else 0
        logger.info(f"[ycs:graph:resolve] merged {merged_count} exact duplicates")
    except Exception as e:
        logger.warning(f"[ycs:graph:resolve] exact merge failed: {e}")

    # Fuzzy merge with semantic gate (per label, skip numeric).
    # Pipeline per label:
    #   a) fuzz.ratio pre-filter at params.FUZZ_MERGE_CUTOFF (75) — fast,
    #      kills the obviously-different pairs.
    #   b) NIM BGE-M3 embedding cosine gate at params.EMBED_COSINE_CUTOFF
    #      (0.85) — catches false-positive fuzz matches like
    #      `Astronomia`↔`Gastronomia` (85.7% fuzz but cos 0.597).
    #      Embeddings are batched once per label so we make at most
    #      one NIM call per label even if it has 100 candidates.
    #   c) Cypher mergeNodes on the survivors.
    # If (b) fails (NIM outage), the label silently falls back to
    # fuzz-only behavior — `_embed_ids_for_resolution` returns `{}`
    # and `cosine_similarity` against empty vectors returns 0.0,
    # which fails the gate → all merges in that label are skipped.
    # Conservative-by-default: prefer losing legitimate merges over
    # introducing semantic confusions.
    try:
        entities = neo4j_graph.query(
            f"MATCH (n:__Entity__:{params.SOURCE_LABEL}) "
            "WHERE n.id IS NOT NULL AND n.id <> '' "
            "UNWIND labels(n) AS label "
            "WITH label, n.id AS id "
            "WHERE label <> '__Entity__' AND label <> 'Document' "
            "RETURN label, collect(DISTINCT id) AS ids"
        )
        for row in entities:
            label = row["label"]
            if domain.should_skip_fuzzy_label(label):
                continue
            ids = [str(i) for i in row["ids"] if isinstance(i, str)]
            if len(ids) < 2:
                continue
            # (b) — embed all ids for this label in ONE batch up
            # front. Cached per-label so the inner cosine check is
            # zero-network. `{}` on NIM failure → gate always fails →
            # no merges in this label (safe fallback).
            embeddings = await _embed_ids_for_resolution(ids)
            already_merged: set[str] = set()
            for i, id1 in enumerate(ids):
                if id1 in already_merged:
                    continue
                for id2 in ids[i + 1:]:
                    if id2 in already_merged:
                        continue
                    # Deterministic canonical-form check: BGE-M3 cosine is unreliable on short strings (0.81 < 0.85 cutoff).
                    if domain.is_obvious_merge(id1, id2):
                        canonical, duplicate = domain.pick_canonical(id1, id2)
                        try:
                            neo4j_graph.query(
                                f"MATCH (n1:`{label}`:{params.SOURCE_LABEL} {{id: $canonical}}), "
                                f"      (n2:`{label}`:{params.SOURCE_LABEL} {{id: $duplicate}}) "
                                "CALL apoc.refactor.mergeNodes([n1, n2], "
                                "  {properties: 'discard', mergeRels: true}) "
                                "YIELD node "
                                "RETURN node",
                                params = {
                                    "canonical": canonical,
                                    "duplicate": duplicate,
                                },
                            )
                            already_merged.add(duplicate)
                            merged_count += 1
                            logger.info(
                                f"[ycs:graph:resolve] obvious-merge "
                                f"'{duplicate}' → '{canonical}' "
                                f"(case/accent/whitespace-only)"
                            )
                        except Exception:
                            pass
                        continue
                    # (a) fuzz pre-filter
                    score = fuzz.ratio(id1, id2)
                    if not (params.FUZZ_MERGE_CUTOFF <= score < 100):
                        continue
                    # (b) semantic gate
                    vec_a = embeddings.get(id1, [])
                    vec_b = embeddings.get(id2, [])
                    cosine = domain.cosine_similarity(vec_a, vec_b)
                    if not domain.should_merge_by_cosine(cosine):
                        logger.info(
                            f"[ycs:graph:resolve] semantic-skip "
                            f"'{id1}' ↔ '{id2}' fuzz={score}% "
                            f"cos={cosine:.3f}<{params.EMBED_COSINE_CUTOFF}"
                        )
                        continue
                    canonical, duplicate = domain.pick_canonical(id1, id2)
                    try:
                        neo4j_graph.query(
                            f"MATCH (n1:`{label}`:{params.SOURCE_LABEL} {{id: $canonical}}), "
                            f"      (n2:`{label}`:{params.SOURCE_LABEL} {{id: $duplicate}}) "
                            "CALL apoc.refactor.mergeNodes([n1, n2], "
                            "  {properties: 'discard', mergeRels: true}) "
                            "YIELD node "
                            "RETURN node",
                            params = {
                                "canonical": canonical,
                                "duplicate": duplicate,
                            },
                        )
                        already_merged.add(duplicate)
                        merged_count += 1
                        logger.info(
                            f"[ycs:graph:resolve] fuzzy '{duplicate}' → "
                            f"'{canonical}' (fuzz={score}% cos={cosine:.3f})"
                        )
                    except Exception:
                        pass
    except Exception as e:
        logger.warning(f"[ycs:graph:resolve] fuzzy merge failed: {e}")

    return merged_count



async def discover_schema(
    sample_transcripts: list[str], llm: Any,
) -> dict:
    """LLM-suggested allowed_nodes/allowed_relationships from sample transcripts (AutoSchemaKG-style, optional)."""
    samples = "\n\n---\n\n".join(
        sample_transcripts[:params.SCHEMA_DISCOVERY_SAMPLE_COUNT]
    )
    chain = prompts.SCHEMA_DISCOVERY_PROMPT | llm.with_structured_output(
        schemas.SchemaDiscovery, method = "function_calling",
    )
    result = await chain.ainvoke(
        {"samples": samples[:params.SCHEMA_DISCOVERY_SAMPLE_CHAR_CAP]},
    )
    return {
        "allowed_nodes":          result.allowed_nodes,
        "allowed_relationships":  result.allowed_relationships,
        "instructions":           result.extraction_focus,
    }



async def get_graph_stats(neo4j_graph: Neo4jGraph) -> dict:
    """Cypher counts grouped by label / type. Scoped to params.SOURCE_LABEL —
    this is surfaced as YCS's own graph-size stats (api/v1/ycs/agents),
    not a whole-instance admin view, so it must not count a future
    second project's nodes sharing this same Neo4j CE instance."""
    nodes_result = neo4j_graph.query(
        f"MATCH (n:{params.SOURCE_LABEL}) "
        "UNWIND labels(n) AS label "
        "RETURN label, count(*) AS count "
        "ORDER BY count DESC"
    )
    nodes_by_label = {row["label"]: row["count"] for row in nodes_result}
    rels_result = neo4j_graph.query(
        f"MATCH (a:{params.SOURCE_LABEL})-[r]->(b:{params.SOURCE_LABEL}) "
        "RETURN type(r) AS type, count(*) AS count "
        "ORDER BY count DESC"
    )
    rels_by_type = {row["type"]: row["count"] for row in rels_result}
    return {
        "total_nodes":           sum(nodes_by_label.values()),
        "total_relationships":   sum(rels_by_type.values()),
        "nodes_by_label":        nodes_by_label,
        "relationships_by_type": rels_by_type,
    }


def build_video_metadata_graph(
    neo4j_graph: Neo4jGraph,
    videos: list[dict],
) -> None:
    """`MERGE Video {id}` + `MERGE Channel {id}` + `(Video)-[:BELONGS_TO]->(Channel)`.
    No LLM call — pure metadata pass before the entity extraction.
    Video/Channel carry params.PROJECT_LABEL/params.SOURCE_LABEL from creation (see
    params.py) — unlike Document/__Entity__, these are only ever MERGEd
    here, so the tag can go straight into the pattern instead of a
    follow-up query."""
    for video in videos:
        neo4j_graph.query(
            f"MERGE (v:Video:{params.PROJECT_LABEL}:{params.SOURCE_LABEL} {{id: $id}}) "
            "SET v.title = $title, "
            "    v.upload_date = $upload_date, "
            "    v.webpage_url = $webpage_url",
            params = {
                "id":          video.get("video_id", ""),
                "title":       video.get("title", ""),
                "upload_date": video.get("upload_date", ""),
                "webpage_url": video.get("webpage_url", ""),
            },
        )
        channel = video.get("channel", "")
        channel_id = video.get("channel_id", "")
        if channel and channel_id:
            neo4j_graph.query(
                f"MERGE (c:Channel:{params.PROJECT_LABEL}:{params.SOURCE_LABEL} {{id: $channel_id}}) "
                "SET c.name = $channel_name "
                "WITH c "
                f"MATCH (v:Video:{params.SOURCE_LABEL} {{id: $video_id}}) "
                "MERGE (v)-[:BELONGS_TO]->(c)",
                params = {
                    "channel_id":   channel_id,
                    "channel_name": channel,
                    "video_id":     video.get("video_id", ""),
                },
            )


def delete_documents_for_videos(
    neo4j_graph: Neo4jGraph,
    video_ids:   list[str],
) -> dict[str, int]:
    """Best-effort delete of Phase-3 Document nodes (per-video transcript
    holders) + the Video metadata nodes for the supplied `video_ids`,
    followed by a SCOPED orphan-entity sweep.

    Used by the Pipeline panel's `Wipe cache` button + the Library's
    per-row trash + bulk-delete buttons so the wiped videos disappear
    from every store WITHOUT leaving dangling entity nodes around or
    affecting any video still present.

    Scoped deletes:
      - `Document` nodes whose `video_id` OR `parent_video_id` is in the
        list (DETACH DELETE drops their MENTIONS edges to entities
        cleanly). 2026-09-15: added the `parent_video_id` half — a split
        video's Document nodes carry `video_id="XYZ#p{n}"`, never the
        parent id, so deleting "XYZ" alone previously left every
        partition's Document (and the entities it uniquely mentioned)
        behind forever.
      - `Video` metadata nodes whose `id` is in the list.
      - `__Entity__` nodes that were mentioned by the deleted Documents
        AND have ZERO remaining MENTIONS edges from any other Document.
        Pre-collected before the Document delete so we never sweep an
        entity that was already orphaned by an earlier wipe — keeps the
        operation idempotent for the requested scope. Entity-to-entity
        edges (HAS_CHARACTERISTIC, LOCATED_IN, etc.) cascade via DETACH
        DELETE; shared entities (still mentioned by surviving Documents)
        are untouched.

    Best-effort: Neo4j hiccup is logged + counted, never raised — the
    wipe of other stores still proceeds."""
    if not video_ids:
        return {
            "documents_deleted": 0,
            "videos_deleted":    0,
            "entities_swept":    0,
        }
    out: dict[str, Any] = {}

    # Pre-collect: which entities were mentioned by the about-to-be-
    # wiped Documents? Captured BEFORE the delete so we know exactly
    # which entities to revisit for orphan-status after the delete.
    candidate_ids: list[str] = []
    try:
        cand = neo4j_graph.query(
            f"MATCH (d:Document:{params.SOURCE_LABEL})-[:MENTIONS]->(e:__Entity__:{params.SOURCE_LABEL}) "
            "WHERE d.video_id IN $vids OR d.parent_video_id IN $vids "
            "RETURN collect(DISTINCT elementId(e)) AS ids",
            params = {"vids": list(video_ids)},
        )
        candidate_ids = list(cand[0]["ids"]) if cand and cand[0].get("ids") else []
    except Exception as e:
        logger.warning(
            f"[ycs:graph:wipe] orphan-candidate collection failed: "
            f"{type(e).__name__}: {str(e)[:200]}"
        )

    try:
        doc_result = neo4j_graph.query(
            f"MATCH (d:Document:{params.SOURCE_LABEL}) "
            "WHERE d.video_id IN $vids OR d.parent_video_id IN $vids "
            "WITH d, count(d) AS _ "
            "DETACH DELETE d "
            "RETURN count(*) AS deleted",
            params = {"vids": list(video_ids)},
        )
        n_docs = int(doc_result[0]["deleted"]) if doc_result else 0
        out["documents_deleted"] = n_docs
        logger.info(f"[ycs:graph:wipe] deleted {n_docs} Document node(s)")
    except Exception as e:
        out["documents_deleted"] = 0
        out["documents_error"]   = str(e)[:200]
        logger.warning(
            f"[ycs:graph:wipe] Document delete failed: "
            f"{type(e).__name__}: {str(e)[:200]}"
        )
    try:
        vid_result = neo4j_graph.query(
            f"MATCH (v:Video:{params.SOURCE_LABEL}) WHERE v.id IN $vids "
            "DETACH DELETE v "
            "RETURN count(*) AS deleted",
            params = {"vids": list(video_ids)},
        )
        n_vids = int(vid_result[0]["deleted"]) if vid_result else 0
        out["videos_deleted"] = n_vids
        logger.info(f"[ycs:graph:wipe] deleted {n_vids} Video node(s)")
    except Exception as e:
        out["videos_deleted"] = 0
        out["videos_error"]   = str(e)[:200]
        logger.warning(
            f"[ycs:graph:wipe] Video delete failed: "
            f"{type(e).__name__}: {str(e)[:200]}"
        )

    # Scoped orphan sweep: only the candidates collected above that
    # now have ZERO :MENTIONS incoming. Skips when there were no
    # candidates (no Documents existed) so we never accidentally
    # sweep entities from unrelated runs.
    n_swept = 0
    if candidate_ids:
        try:
            sweep = neo4j_graph.query(
                f"MATCH (e:__Entity__:{params.SOURCE_LABEL}) "
                "WHERE elementId(e) IN $ids "
                "AND NOT EXISTS { MATCH (:Document)-[:MENTIONS]->(e) } "
                "DETACH DELETE e "
                "RETURN count(*) AS swept",
                params = {"ids": candidate_ids},
            )
            n_swept = int(sweep[0]["swept"]) if sweep else 0
            logger.info(
                f"[ycs:graph:wipe] swept {n_swept}/{len(candidate_ids)} "
                f"orphan __Entity__ node(s) (mentioned only by deleted "
                f"Documents)"
            )
        except Exception as e:
            out["entities_error"] = str(e)[:200]
            logger.warning(
                f"[ycs:graph:wipe] orphan sweep failed: "
                f"{type(e).__name__}: {str(e)[:200]}"
            )
    out["entities_swept"] = n_swept
    return out
