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

import logging
import time
from typing import Any, Callable

from langchain_core.documents import Document
from langchain_experimental.graph_transformers import LLMGraphTransformer
from langchain_neo4j import Neo4jGraph
from rapidfuzz import fuzz

from . import domain
from .params import (
    DEFAULT_BATCH_SIZE,
    FUZZ_MERGE_CUTOFF,
    INTER_BATCH_SLEEP_S,
    SCHEMA_DISCOVERY_SAMPLE_CHAR_CAP,
    SCHEMA_DISCOVERY_SAMPLE_COUNT,
)
from .prompts import EXTRACTION_INSTRUCTIONS, SCHEMA_DISCOVERY_PROMPT
from .schemas import SchemaDiscovery


logger = logging.getLogger(__name__)


# ---------- factory ------------------------------------------------------

def create_graph_transformer(llm: Any) -> LLMGraphTransformer:
    """No `allowed_nodes` / `allowed_relationships` — the LLM captures
    everything it finds. `additional_instructions` enforce FORMATTING
    only (TitleCase nodes, UPPER_SNAKE_CASE relationships); entity
    resolution cleans up after the fact."""
    return LLMGraphTransformer(
        llm = llm,
        node_properties = True,
        relationship_properties = True,
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
) -> dict:
    """One LLM call PER TRANSCRIPT (not per chunk). Deprecated rationale:
    full context → +30% entity quality vs chunked, and 352 calls instead
    of 2911 for a 352-video corpus.

    Idempotent — skips any video whose `video_id` is already tagged on
    a Document node in Neo4j.

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
    if progress_cb:
        progress_cb({
            "phase":         "extracting",
            "current":       0,
            "total":         len(documents),
            "current_batch": 0,
            "total_batches": total_batches,
            "nodes":         0,
            "rels":          0,
        })

    # Batch loop with rate-limit pacing.
    for batch_start in range(0, len(documents), batch_size):
        batch = documents[batch_start:batch_start + batch_size]
        try:
            graph_documents = await transformer.aconvert_to_graph_documents(batch)
            neo4j_graph.add_graph_documents(
                graph_documents,
                include_source = True,
                baseEntityLabel = True,
            )
            # Tag Document nodes with their video_id (used by the
            # skip-on-re-run check above on a future call).
            for doc in batch:
                vid = doc.metadata.get("video_id", "")
                if not vid:
                    continue
                try:
                    neo4j_graph.query(
                        "MATCH (d:Document) WHERE d.text CONTAINS $title "
                        "SET d.video_id = $video_id",
                        params = {
                            "title":    (doc.metadata.get("title") or "")[:50],
                            "video_id": vid,
                        },
                    )
                except Exception:
                    pass
            for gdoc in graph_documents:
                total_nodes += len(gdoc.nodes)
                total_relationships += len(gdoc.relationships)
            total_processed += len(batch)
            logger.info(
                f"[ycs:graph] batch {batch_start // batch_size + 1}: "
                f"{total_processed}/{len(documents)} transcripts, "
                f"{total_nodes} nodes, {total_relationships} rels"
            )
        except Exception as e:
            logger.warning(
                f"[ycs:graph] batch {batch_start // batch_size + 1} failed: "
                f"{type(e).__name__}: {str(e)[:200]}. Continuing."
            )
            total_processed += len(batch)
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

    # Step 1 — normalize ids to lowercase.
    try:
        result = neo4j_graph.query(
            "MATCH (n:__Entity__) "
            "WHERE n.id IS NOT NULL AND n.id <> toLower(trim(n.id)) "
            "SET n.id = toLower(trim(n.id)) "
            "RETURN count(n) AS normalized"
        )
        normalized = result[0]["normalized"] if result else 0
        logger.info(f"[ycs:graph:resolve] normalized {normalized} ids")
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

    # Step 3 — rapidfuzz fuzzy merge (per label, skip numeric labels).
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
            already_merged: set[str] = set()
            for i, id1 in enumerate(ids):
                if id1 in already_merged:
                    continue
                for id2 in ids[i + 1:]:
                    if id2 in already_merged:
                        continue
                    score = fuzz.ratio(id1, id2)
                    # 100 = exact, already handled by Step 2.
                    if not (FUZZ_MERGE_CUTOFF <= score < 100):
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
                            f"'{canonical}' ({score}%)"
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
