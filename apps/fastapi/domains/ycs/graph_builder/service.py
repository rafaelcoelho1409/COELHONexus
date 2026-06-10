"""ycs/graph_builder — async LLM → Neo4j entity-graph pipeline.

Imperative Shell (`docs/CODE-CONVENTIONS.md` §4): I/O + Cypher writes +
LLM dispatch. Pure decisions delegated to `domain.py`.

Direct port of deprecated `services/youtube/graph_builder.py:L33-351`.

Public API:
  create_graph_transformer(llm) → LLMGraphTransformer
  extract_and_store_graph(transcripts, metadata_map, llm, neo4j_graph, batch_size)
  resolve_entities(neo4j_graph) → int (merged count)
  discover_schema(sample_transcripts, llm) → dict
  get_graph_stats(neo4j_graph) → dict
  build_video_metadata_graph(neo4j_graph, videos)
"""
from __future__ import annotations

import asyncio
import logging
import time
from typing import Any, Callable

from langchain_core.documents import Document
from langchain_experimental.graph_transformers import LLMGraphTransformer
from langchain_neo4j import Neo4jGraph
from rapidfuzz import fuzz

from domains.ycs.embeddings import NVIDIAEmbeddings

from . import domain
from .params import (
    DEFAULT_BATCH_SIZE,
    EMBED_COSINE_CUTOFF,
    FUZZ_MERGE_CUTOFF,
    GRAPH_BATCH_TIMEOUT_S,
    INTER_BATCH_SLEEP_S,
    RESOLVE_EMBED_MODEL,
    SCHEMA_DISCOVERY_SAMPLE_CHAR_CAP,
    SCHEMA_DISCOVERY_SAMPLE_COUNT,
)
from .prompts import EXTRACTION_INSTRUCTIONS, SCHEMA_DISCOVERY_PROMPT
from .schemas import SchemaDiscovery


# Lazy singleton — re-used across resolve_entities calls within the
# same Celery worker process. NVIDIAEmbeddings owns its own httpx
# client + retry/backoff, so once warm it's free to reuse.
_resolve_embedder: NVIDIAEmbeddings | None = None


def _get_resolve_embedder() -> NVIDIAEmbeddings:
    global _resolve_embedder
    if _resolve_embedder is None:
        _resolve_embedder = NVIDIAEmbeddings(model = RESOLVE_EMBED_MODEL)
    return _resolve_embedder


def _embed_ids_for_resolution(ids: list[str]) -> dict[str, list[float]]:
    """Embed a batch of entity-id strings via NIM BGE-M3, returning a
    `{id: vector}` map for downstream cosine comparisons.

    Best-effort: any NIM hiccup logs a warning and returns `{}` — the
    caller falls back to fuzz-only behavior (drops the semantic gate
    but doesn't crash entity resolution). This degrades correctness
    silently (a NIM outage could let through false merges), but
    preserves availability — same tradeoff as Steps 1+2's wide
    try/except guards."""
    if not ids:
        return {}
    try:
        vecs = _get_resolve_embedder().embed_documents(ids)
    except Exception as e:
        logger.warning(
            f"[ycs:graph:resolve] NIM embedding failed; falling back "
            f"to fuzz-only merge for this label "
            f"({type(e).__name__}: {str(e)[:120]})"
        )
        return {}
    return {ids[i]: vecs[i] for i in range(min(len(ids), len(vecs)))}


logger = logging.getLogger(__name__)


# ---------- factory ------------------------------------------------------

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
      - Keeps `additional_instructions` (our EXTRACTION_INSTRUCTIONS)
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
        additional_instructions = EXTRACTION_INSTRUCTIONS,
    )


# ---------- main pipeline ------------------------------------------------

async def extract_and_store_graph(
    transcripts: list[dict],
    metadata_map: dict,
    llm: Any,
    neo4j_graph: Neo4jGraph,
    batch_size: int = DEFAULT_BATCH_SIZE,
    progress_cb: Callable[[dict[str, Any]], None] | None = None,
    abort_after_consecutive: int = 0,
) -> dict:
    """One LLM call PER TRANSCRIPT (not per chunk). Deprecated rationale:
    full context → +30% entity quality vs chunked, and 352 calls instead
    of 2911 for a 352-video corpus.

    Idempotent — skips any video whose `video_id` is already tagged on
    a Document node in Neo4j.

    `abort_after_consecutive` > 0 arms the circuit breaker: after that
    many consecutive non-productive batches (raised OR 0 nodes + 0 rels)
    the loop stops and the stats carry `aborted_nonproductive=True` so
    the caller (neo4j_task) can re-pick a different arm and call again —
    idempotency means completed videos are skipped and failed ones are
    retried on the new arm. 0 disables (full-run behavior).

    Returns counters dict suitable for the API response envelope."""
    transformer = create_graph_transformer(llm)
    total_nodes = 0
    total_relationships = 0
    total_processed = 0
    total_skipped = 0

    # Skip-on-re-run: query Neo4j for already-processed video_ids.
    already_processed: set[str] = set()
    try:
        result = neo4j_graph.query(
            "MATCH (d:Document) WHERE d.video_id IS NOT NULL "
            "RETURN collect(DISTINCT d.video_id) AS processed_ids"
        )
        if result and result[0].get("processed_ids"):
            already_processed = set(result[0]["processed_ids"])
            logger.info(
                f"[ycs:graph] {len(already_processed)} videos already in Neo4j; skip"
            )
    except Exception:
        pass

    # Build one Document per fresh transcript (full text, NIM models
    # support 128K tokens — no truncation).
    documents: list[Document] = []
    for transcript in transcripts:
        vid = transcript["video_id"]
        if vid in already_processed:
            total_skipped += 1
            continue
        content = transcript.get("content") or ""
        if not content.strip():
            continue
        meta = metadata_map.get(vid, {})
        documents.append(
            Document(
                page_content = content,
                metadata = {
                    "video_id": vid,
                    "title":    meta.get("title", ""),
                    "channel":  meta.get("channel", ""),
                },
            ),
        )

    logger.info(
        f"[ycs:graph] processing {len(documents)} transcripts "
        f"(skipped {total_skipped})"
    )

    total_batches = (len(documents) + batch_size - 1) // batch_size
    # Per-video status tracking for the Ingest-page right-column list.
    # With `pipeline_task.NEO4J_BATCH_SIZE=1` each batch is one video,
    # so completed_ids / failed_ids advance per video, matching Phase 1
    # and Phase 2's granularity.
    completed_ids: list[str] = []
    failed_ids:    list[str] = []
    if progress_cb:
        progress_cb({
            "phase":         "extracting",
            "current":       0,
            "total":         len(documents),
            "current_batch": 0,
            "total_batches": total_batches,
            "nodes":         0,
            "rels":          0,
            "completed_ids": list(completed_ids),
            "failed_ids":    list(failed_ids),
        })

    # Track the LAST per-batch exception so the silent-zero guard
    # downstream can surface the actual LLM error body in the log
    # (otherwise the user only sees "0 nodes" with no diagnostic).
    last_batch_error: str | None = None
    # Circuit-breaker state — see `abort_after_consecutive` docstring.
    consecutive_nonproductive = 0
    aborted_nonproductive = False
    # Batch loop with rate-limit pacing.
    for batch_start in range(0, len(documents), batch_size):
        batch = documents[batch_start:batch_start + batch_size]
        batch_nodes_before = total_nodes
        batch_rels_before = total_relationships
        batch_raised = False
        try:
            # Watchdog: hard wall-clock ceiling per batch. The inner
            # request stack already has per-deployment timeouts + a
            # zero-timeout-retry policy (see _build_pinned_chain), so
            # this only fires when that stack wedges — and guarantees
            # one slow arm can't burn the whole run before the bandit
            # gets its negative reward.
            graph_documents = await asyncio.wait_for(
                transformer.aconvert_to_graph_documents(batch),
                timeout = GRAPH_BATCH_TIMEOUT_S,
            )
            # Tier-2 fix `B` (2026-06-07) — coerce node ids at the
            # source. `LLMGraphTransformer` occasionally emits an `id`
            # as a `StringArray` (Python list of alternate-name
            # strings the LLM saw across the transcript) instead of a
            # single string. Once that lands in Neo4j it breaks
            # Step 1's Cypher `trim()` (`Expected a string value for
            # trim, but got: StringArray[Gastronomia, Astronomia]`),
            # which kills the entire normalize pass. Fixing it here
            # at the source means Step 1 always sees clean string
            # ids. Dropping nodes whose id coerces to "" so we don't
            # write graph rubbish.
            for gdoc in graph_documents:
                clean_nodes = []
                for node in gdoc.nodes:
                    node.id = domain.coerce_entity_id(node.id)
                    if node.id:
                        clean_nodes.append(node)
                gdoc.nodes = clean_nodes
            neo4j_graph.add_graph_documents(
                graph_documents,
                include_source = True,
                baseEntityLabel = True,
            )
            # video_id tagging happens NATIVELY inside
            # add_graph_documents: langchain-neo4j's include_source
            # path runs `SET d += $document.metadata`, and our source
            # Documents carry {video_id, title, channel}. The previous
            # explicit `WHERE d.text CONTAINS $title` tag pass was
            # removed 2026-06-10 — it was redundant for productive
            # batches AND harmful for empty ones: a 0-node batch would
            # stamp its video_id onto OTHER videos' pre-existing
            # Documents (CONTAINS false-positive), overwriting their
            # tags and falsely marking unextracted videos as done,
            # which broke the arm-swap resume's skip check.
            for gdoc in graph_documents:
                total_nodes += len(gdoc.nodes)
                total_relationships += len(gdoc.relationships)
            total_processed += len(batch)
            # Per-video bookkeeping for the Ingest-page video list.
            # With batch_size=1 this advances one id per iteration; with
            # the legacy batch_size=3 path it bulk-adds the whole batch.
            for doc in batch:
                vid = doc.metadata.get("video_id", "")
                if vid and vid not in completed_ids:
                    completed_ids.append(vid)
            logger.info(
                f"[ycs:graph] batch {batch_start // batch_size + 1}: "
                f"{total_processed}/{len(documents)} transcripts, "
                f"{total_nodes} nodes, {total_relationships} rels"
            )
        except Exception as e:
            batch_raised = True
            if isinstance(e, TimeoutError) and not str(e):
                # asyncio.wait_for raises a bare TimeoutError — stamp it
                # so the silent-zero guard's diagnostic isn't empty.
                last_batch_error = (
                    f"TimeoutError: batch exceeded the "
                    f"{GRAPH_BATCH_TIMEOUT_S:.0f}s watchdog"
                )
            else:
                last_batch_error = f"{type(e).__name__}: {str(e)[:400]}"
            logger.warning(
                f"[ycs:graph] batch {batch_start // batch_size + 1} failed: "
                f"{last_batch_error}. Continuing."
            )
            total_processed += len(batch)
            for doc in batch:
                vid = doc.metadata.get("video_id", "")
                if vid and vid not in failed_ids:
                    failed_ids.append(vid)
        # Circuit breaker: raised OR wrote nothing → non-productive.
        produced = (total_nodes > batch_nodes_before
                    or total_relationships > batch_rels_before)
        if batch_raised or not produced:
            consecutive_nonproductive += 1
        else:
            consecutive_nonproductive = 0
        if (abort_after_consecutive > 0
                and consecutive_nonproductive >= abort_after_consecutive):
            aborted_nonproductive = True
            logger.warning(
                f"[ycs:graph] circuit breaker: {consecutive_nonproductive} "
                f"consecutive non-productive batches — aborting this arm "
                f"so the caller can swap. "
                f"({total_processed}/{len(documents)} attempted, "
                f"{total_nodes} nodes so far)"
            )
            break
        # Per-batch progress emission so the FastHTML Neo4j bar advances
        # in real time. `current` counts attempted (not just succeeded)
        # transcripts so the bar fills monotonically even when an
        # individual batch raises (e.g. transient LLM 5xx).
        if progress_cb:
            last_vid = batch[-1].metadata.get("video_id", "") if batch else ""
            last_meta = metadata_map.get(last_vid, {}) if last_vid else {}
            progress_cb({
                "phase":         "extracting",
                "current":       total_processed,
                "total":         len(documents),
                "current_batch": batch_start // batch_size + 1,
                "total_batches": total_batches,
                "nodes":         total_nodes,
                "rels":          total_relationships,
                "completed_ids": list(completed_ids),
                "failed_ids":    list(failed_ids),
                "current_item": {
                    "id":      last_vid,
                    "title":   last_meta.get("title", ""),
                    "channel": last_meta.get("channel", ""),
                } if last_vid else None,
            })
        # Inter-batch pacing. Deprecated used `time.sleep` (sync) here
        # despite the function being `async def` — preserved verbatim.
        if batch_start + batch_size < len(documents):
            time.sleep(INTER_BATCH_SLEEP_S)

    # Entity resolution is skipped on a circuit-breaker abort — the
    # caller is about to re-run on a fresh arm, and resolution is a
    # global pass over Neo4j; the final (non-aborted) segment runs it
    # once for everything written so far.
    resolved = 0
    if not aborted_nonproductive:
        if progress_cb:
            progress_cb({
                "phase":   "resolving",
                "current": len(documents),
                "total":   len(documents),
                "nodes":   total_nodes,
                "rels":    total_relationships,
            })
        logger.info("[ycs:graph] entity resolution starting")
        resolved = resolve_entities(neo4j_graph)
        logger.info(f"[ycs:graph] entity resolution: {resolved} nodes merged")

    return {
        "documents_processed":   total_processed,
        "nodes_created":         total_nodes,
        "relationships_created": total_relationships,
        "entities_merged":       resolved,
        # Surface the most-recent per-batch LLM exception so the
        # neo4j_task's silent-zero guard can log the body (otherwise
        # the user only sees "0 nodes" with no diagnostic). None when
        # every batch succeeded.
        "last_batch_error":      last_batch_error,
        # Circuit-breaker verdict for the arm-swap loop in neo4j_task.
        "aborted_nonproductive": aborted_nonproductive,
    }


# ---------- entity resolution -------------------------------------------

def resolve_entities(neo4j_graph: Neo4jGraph) -> int:
    """Three-pass deduplication of `__Entity__` nodes:

      1. Lowercase + trim every id.
      2. Cypher MERGE exact duplicates per `(label, id)`.
      3. rapidfuzz fuzzy merge at `FUZZ_MERGE_CUTOFF` (75) per label,
         skipping NUMERIC_LABELS_SKIP where lexical similarity ≠ semantic
         identity.

    Returns the count of nodes merged. Best-effort: per-step failures
    are logged and skipped — the graph stays usable even if APOC isn't
    installed."""
    merged_count = 0

    # Step 1 — normalize ids to canonical form (Tier-2 fix `F`,
    # 2026-06-07). Was a single Cypher statement using `trim()` +
    # `toLower()`, but `trim()` blows up the moment ANY node has a
    # non-string id (e.g., `StringArray[Gastronomia, Astronomia]`
    # emitted by LLMGraphTransformer) and the whole pass bails. We
    # do the normalization in Python now:
    #   1. Pull every entity's elementId + raw id.
    #   2. Compute `normalize_entity_id` (handles non-string types
    #      via `coerce_entity_id`, plus lowercase + NFKD-strip-accents
    #      + whitespace-collapse).
    #   3. UNWIND-batched UPDATE for only the ids that actually
    #      changed.
    # No more Cypher type errors; accent-stripping also catches
    # `Petróleo` ↔ `Petroleo` here so Step 2's exact MERGE collapses
    # them without Step 3 ever needing to think about it.
    try:
        rows = neo4j_graph.query(
            "MATCH (n:__Entity__) WHERE n.id IS NOT NULL "
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

    # Step 2 — exact merge (same label + same normalized id).
    try:
        result = neo4j_graph.query(
            "MATCH (n1:__Entity__), (n2:__Entity__) "
            "WHERE n1 <> n2 AND n1.id = n2.id "
            "AND any(label IN labels(n1) WHERE label IN labels(n2) AND label <> '__Entity__') "
            "WITH n1, collect(DISTINCT n2) AS duplicates "
            "WHERE size(duplicates) > 0 "
            "CALL apoc.refactor.mergeNodes([n1] + duplicates, "
            "  {properties: 'combine', mergeRels: true}) YIELD node "
            "RETURN count(node) AS merged"
        )
        merged_count = result[0]["merged"] if result else 0
        logger.info(f"[ycs:graph:resolve] merged {merged_count} exact duplicates")
    except Exception as e:
        logger.warning(f"[ycs:graph:resolve] exact merge failed: {e}")

    # Step 3 — fuzzy merge with semantic gate (per label, skip numeric).
    # Pipeline per label:
    #   a) fuzz.ratio pre-filter at FUZZ_MERGE_CUTOFF (75) — fast,
    #      kills the obviously-different pairs.
    #   b) NIM BGE-M3 embedding cosine gate at EMBED_COSINE_CUTOFF
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
            "MATCH (n:__Entity__) "
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
            embeddings = _embed_ids_for_resolution(ids)
            already_merged: set[str] = set()
            for i, id1 in enumerate(ids):
                if id1 in already_merged:
                    continue
                for id2 in ids[i + 1:]:
                    if id2 in already_merged:
                        continue
                    # Tier-2 fix `E` (2026-06-07) — obvious-merge
                    # shortcut. If two ids have IDENTICAL canonical
                    # forms (case+accent+whitespace-only diff), merge
                    # them unconditionally — skip the fuzz + cosine
                    # gates entirely. Handles `Donald Trump` ↔
                    # `donald trump` and `Petróleo` ↔ `Petroleo` even
                    # if Step 1 + Step 2 missed them. BGE-M3's
                    # short-string cosine is unreliable here (`Donald
                    # Trump` gets 0.81, below the 0.85 cutoff), so we
                    # rely on the deterministic Python check instead.
                    if domain.is_obvious_merge(id1, id2):
                        canonical, duplicate = domain.pick_canonical(id1, id2)
                        try:
                            neo4j_graph.query(
                                f"MATCH (n1:`{label}` {{id: $canonical}}), "
                                f"      (n2:`{label}` {{id: $duplicate}}) "
                                "CALL apoc.refactor.mergeNodes([n1, n2], "
                                "  {properties: 'combine', mergeRels: true}) "
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
                    if not (FUZZ_MERGE_CUTOFF <= score < 100):
                        continue
                    # (b) semantic gate
                    vec_a = embeddings.get(id1, [])
                    vec_b = embeddings.get(id2, [])
                    cosine = domain.cosine_similarity(vec_a, vec_b)
                    if not domain.should_merge_by_cosine(cosine):
                        logger.info(
                            f"[ycs:graph:resolve] semantic-skip "
                            f"'{id1}' ↔ '{id2}' fuzz={score}% "
                            f"cos={cosine:.3f}<{EMBED_COSINE_CUTOFF}"
                        )
                        continue
                    canonical, duplicate = domain.pick_canonical(id1, id2)
                    try:
                        neo4j_graph.query(
                            f"MATCH (n1:`{label}` {{id: $canonical}}), "
                            f"      (n2:`{label}` {{id: $duplicate}}) "
                            "CALL apoc.refactor.mergeNodes([n1, n2], "
                            "  {properties: 'combine', mergeRels: true}) "
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


# ---------- schema discovery (optional, deprecated 1:1) -----------------

async def discover_schema(
    sample_transcripts: list[str], llm: Any,
) -> dict:
    """LLM-suggested schema from sample transcripts. AutoSchemaKG-style
    soft schema (95% alignment with hand-crafted). Optional — the
    deprecated graph_builder defaults to schema-free extraction; this
    is here for callers that want a curated `allowed_nodes` /
    `allowed_relationships` set."""
    samples = "\n\n---\n\n".join(
        sample_transcripts[:SCHEMA_DISCOVERY_SAMPLE_COUNT]
    )
    chain = SCHEMA_DISCOVERY_PROMPT | llm.with_structured_output(
        SchemaDiscovery, method = "function_calling",
    )
    result = await chain.ainvoke(
        {"samples": samples[:SCHEMA_DISCOVERY_SAMPLE_CHAR_CAP]},
    )
    return {
        "allowed_nodes":          result.allowed_nodes,
        "allowed_relationships":  result.allowed_relationships,
        "instructions":           result.extraction_focus,
    }


# ---------- stats + metadata graph --------------------------------------

async def get_graph_stats(neo4j_graph: Neo4jGraph) -> dict:
    """Cypher counts grouped by label / type."""
    nodes_result = neo4j_graph.query(
        "MATCH (n) "
        "UNWIND labels(n) AS label "
        "RETURN label, count(*) AS count "
        "ORDER BY count DESC"
    )
    nodes_by_label = {row["label"]: row["count"] for row in nodes_result}
    rels_result = neo4j_graph.query(
        "MATCH ()-[r]->() "
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
    No LLM call — pure metadata pass before the entity extraction."""
    for video in videos:
        neo4j_graph.query(
            "MERGE (v:Video {id: $id}) "
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
                "MERGE (c:Channel {id: $channel_id}) "
                "SET c.name = $channel_name "
                "WITH c "
                "MATCH (v:Video {id: $video_id}) "
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
    holders) + the Video metadata nodes for the supplied `video_ids`.
    Used by the Pipeline panel's `Wipe cache` button so the next Phase
    3 doesn't see the `[ycs:graph] N videos already in Neo4j; skip`
    short-circuit on these videos.

    Scoped deletes ONLY:
      - `Document` nodes whose `video_id` is in the list (DETACH DELETE
        drops their MENTIONS edges to entities cleanly).
      - `Video` metadata nodes whose `id` is in the list.

    Entity nodes (`__Entity__`) are LEFT INTACT — they may be referenced
    by other videos' transcripts; deleting them would cascade into
    other videos' graphs. Orphaned entities (no incoming MENTIONS) are
    harmless; a future orphan-sweep pass can clean them if needed.

    Best-effort: Neo4j hiccup is logged + counted, never raised — the
    wipe of other stores still proceeds."""
    if not video_ids:
        return {"documents_deleted": 0, "videos_deleted": 0}
    out: dict[str, int] = {}
    try:
        doc_result = neo4j_graph.query(
            "MATCH (d:Document) WHERE d.video_id IN $vids "
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
            "MATCH (v:Video) WHERE v.id IN $vids "
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
    return out
