"""
Knowledge Distiller — Classical MAP step (deterministic, two-phase)

Drop-in replacement for the LLM-based `_label_shard` in
graphs/knowledge/distiller.py. Same input shape (shards of (slug, content)
tuples), same output type (list[ShardLabels]), but produced via:

    Phase A (all shards in parallel, single embedding model loaded):
        Xinference embeddings (Qwen3-Embedding-0.6B Q8 GGUF, llama.cpp)
            ↓
        community_detection — greedy O(N²) cosine, threshold=0.60
            ↓
        clusters of slug indices per shard (no labels yet)

    Phase B (all shards in parallel, hosted small-LM rotator):
        For each cluster: chat completion via the LLM rotator's `kd-keylm`
        group (NIM meta/llama-3.2-1b-instruct primary, Groq llama-3.2-1b-
        preview fallback). Bounded concurrency to keep free-tier RPM in check.
            ↓
        2-4 word Title-Case label

Architecture rule (memory: project_local_vs_rotator_architecture.md):
    Embeddings + rerankers → Xinference (local, high call volume).
    LLMs of any size → LLM rotator (NIM/Groq/etc, hosted free tier).

    Earlier draft hosted a 1B instruct LM on Xinference too. Reverted on
    2026-05-09 after hitting OOM during failed launch attempts + custom
    model registration friction. The rotator already hosts Llama-3.2-1B-
    Instruct as GA on NIM with a generous free tier — no reason to duplicate.

Why Llama-3.2-1B-Instruct (not Qwen2.5-0.5B / not Qwen3-0.6B):
    - Highest IFEval (59.5) among ≤1B candidates as of May 2026
    - Temp=0 deterministic decoding works without caveat (Qwen3 team
      explicitly warns against greedy decoding for sub-1B models)
    - Distilled from Llama-3.1-405B/70B → strong format adherence at small size
    - See docs/KD-PLANNER-MAP-OPTIMIZATION.md §5 for the full rationale.
"""
import asyncio
import logging
import re
import time
from typing import Optional

import numpy as np
from langchain_core.messages import HumanMessage, SystemMessage

from schemas.knowledge.agents import ShardCluster, ShardLabels
from services.knowledge.embeddings import (
    community_detection,
    embed_texts,
)
from services.llm_chain import build_keylm_chain


logger = logging.getLogger(__name__)


# =============================================================================
# Configuration — committed picks per docs/KD-PLANNER-MAP-OPTIMIZATION.md §5
# =============================================================================
# Phase A embedder is selected by services/llm_chain.py KD_EMBED_GROUP
# (currently NIM nvidia/llama-nemotron-embed-1b-v2 via the rotator).
# embed_texts() goes through that group automatically — no model_name
# kwarg threading needed at the planner level.

# Cosine threshold for community_detection. Lower → coarser clusters (fewer,
# larger). Higher → tighter (more, smaller). 0.60 is the sbert recommendation
# for sentence-pair embeddings; on Qwen3-Embedding-0.6B Q8 GGUF it produces
# 1-3 clusters per N=40 shard.
COMMUNITY_THRESHOLD = 0.60
MIN_COMMUNITY_SIZE = 2

# Snippet length per file fed into the embedder.
#
# Bumped 80 → 1500 on 2026-05-09 night. The original 80 was chosen to keep
# the LLM-path MAP prompt under Groq's 12K TPM free-tier limit (40 docs ×
# 80 chars × 11 shards ≈ 9.6K tokens). That constraint doesn't apply to the
# embedding path — NIM `llama-nemotron-embed-1b-v2` has no per-call token
# cap, only 40 RPM (which we comfortably stay under at ~14 batches/study).
#
# 1500 chars ≈ 450 tokens, which sits in NV-Embed's training-distribution
# sweet spot (256-512 tokens). At 80 chars, embeddings cluster mostly on
# the slug; at 1500 they capture the doc's full intro + first code example
# + first section header — exactly the topical anchors clustering needs.
# Beyond ~1500 chars, technical docs shift to param tables / API enumerations
# which dilute topical signal (plateau on benchmarks).
#
# Sources: NV-Embed-v2 paper §3.1 (training distribution); MTEB Clustering
# benchmark suite (~10% accuracy drop at <100-token inputs); LangChain RAG
# cookbook (512-token chunks recommended); LlamaIndex chunk-size eval.
PREVIEW_CHARS = 1500

# Cap on cluster text fed into the KeyLLM prompt. Keeps each call ~300-500
# input tokens regardless of cluster size — small enough for sub-second
# rotator turn-around without truncating real cluster signal.
LABEL_TEXT_CAP = 1500

# KeyLLM generation tunables — deterministic by design.
KEYLM_MAX_TOKENS = 16  # 2-4 words ≈ 8-12 BPE tokens; +4 buffer for whitespace

# Concurrency cap on KeyLLM rotator calls. Free-tier RPM on the small-LM
# providers (NIM ~40 RPM, Groq generous) is plenty for our ~33 calls/study
# but we cap at 4 to keep the request rate predictable + avoid 429 storms
# on simultaneous shard bursts. The Router's allowed_fails_policy will
# cool down a deployment that 429s anyway, but capping is cheaper.
KEYLM_CONCURRENCY = 4


# Lazy module-level singleton — built on first call, reused thereafter.
# `build_keylm_chain()` is cheap (just constructs a ChatLiteLLMRouter wrapper
# around the shared Router), but caching it skips the LangChain instantiation
# on every cluster-label call.
_keylm_chain_singleton = None
_keylm_chain_lock = asyncio.Lock()


async def _get_keylm_chain():
    """Lazy singleton for the build_keylm_chain() factory result."""
    global _keylm_chain_singleton
    if _keylm_chain_singleton is not None:
        return _keylm_chain_singleton
    async with _keylm_chain_lock:
        if _keylm_chain_singleton is None:
            _keylm_chain_singleton = build_keylm_chain()
    return _keylm_chain_singleton


# =============================================================================
# KeyLLM prompt — single-shot, format-strict
# =============================================================================
# IFEval 59.5 on Llama-3.2-1B-Instruct means it follows formatting rules well.
# We give one explicit constraint set + one short cluster preview. No few-shot
# examples (they bloat the prompt and risk injecting topic bias into outputs).
_KEYLM_SYSTEM = (
    "You output exactly one short title and nothing else. "
    "No preamble, no explanation, no quotes, no punctuation."
)

_KEYLM_USER_TEMPLATE = (
    "Generate a 2-4 word title in Title Case for this cluster of related "
    "documentation snippets. Focus on the technical concept, not the "
    "framework name.\n\n"
    "{cluster_text}\n\n"
    "Title:"
)

# Output sanitization: strip wrappers the LM may emit despite the prompt.
_LEADING_LABEL_RE = re.compile(
    r"^\s*(?:title|label|topic|name)\s*[:\-]\s*",
    re.IGNORECASE,
)
_QUOTES_RE = re.compile(r"^[\"\'`]+|[\"\'`]+$")
# Preserve apostrophes inside words ("Terragrunt's" → "Terragrunt's"); previously
# stripped → "Terragrunt S" which read as a typo. Hyphens still allowed.
_NON_LABEL_CHARS_RE = re.compile(r"[^\w\s\-']")


def _sanitize_label(raw: str, fallback: str) -> str:
    """
    Normalize the LM's output into a 2-4 word Title-Case label.
      - Take only the first non-empty line
      - Strip 'Title:' / 'Label:' / quote wrappers / trailing punctuation
      - Collapse whitespace, drop non-word chars except hyphens
      - Title-case + cap at 6 words (defense-in-depth on token-budget overruns)
    Returns `fallback` if nothing usable remains.
    """
    if not raw:
        return fallback
    line = raw.strip().split("\n", 1)[0].strip()
    line = _LEADING_LABEL_RE.sub("", line)
    line = _QUOTES_RE.sub("", line.strip())
    line = _NON_LABEL_CHARS_RE.sub(" ", line)
    line = " ".join(line.split())  # collapse whitespace
    if not line:
        return fallback
    words = line.split()[:6]
    # Force Title Case across the board. Previously preserved `w.upper()` to
    # respect intentional acronyms (HTTP, IAM, AWS), but KeyLLM also echoes
    # whole-heading ALL CAPS like "TERRAGRUNT HOOKS AND EXECUTION" which we
    # don't want. Trade: real acronyms now Title-cased ("AWS" → "Aws"). For
    # KD's clustering use-case, this trade is fine (REDUCE re-labels chapters
    # via the kd-all rotator anyway, and acronym precision isn't critical
    # for shard-level intermediate signal).
    return " ".join(w.lower().capitalize() for w in words)


# =============================================================================
# Phase A — embed + cluster all shards (uses the rotator's kd-embed group)
# =============================================================================
async def _cluster_shards(
    shards: list[list[tuple[str, str]]],
) -> list[dict]:
    """
    Embed snippets + run community_detection for every shard in parallel.

    Returns a list of dicts (one per shard), each containing:
        - "slugs":     list[str]              — file slugs in shard order
        - "snippets":  list[str]              — text fed into the embedder
        - "communities": list[list[int]]      — cluster member indices
        - "embed_dt":  float                  — wall time for the embed call
    """
    async def _one(shard_entries: list[tuple[str, str]]) -> dict:
        slugs = [slug for slug, _ in shard_entries]
        snippets = [
            f"{slug.replace('-', ' ')} — {(content or '').strip()[:PREVIEW_CHARS]}"
            for slug, content in shard_entries
        ]
        if not snippets:
            return {
                "slugs": slugs, "snippets": snippets,
                "communities": [], "embed_dt": 0.0,
            }
        t0 = time.monotonic()
        vectors_list, _ = await embed_texts(snippets)
        embed_dt = time.monotonic() - t0
        vectors = np.asarray(vectors_list, dtype=np.float32)
        communities = community_detection(
            vectors,
            threshold=COMMUNITY_THRESHOLD,
            min_community_size=MIN_COMMUNITY_SIZE,
        )
        return {
            "slugs": slugs,
            "snippets": snippets,
            "communities": communities,
            "embed_dt": embed_dt,
        }

    return await asyncio.gather(*(_one(s) for s in shards))


# =============================================================================
# Phase B — generate cluster labels via KeyLLM (uses KEYLM_MODEL)
# =============================================================================
async def _label_one_cluster(
    cluster_text: str,
    fallback_label: str,
    semaphore: asyncio.Semaphore,
) -> str:
    """One KeyLLM call via the rotator: returns a sanitized 2-4 word
    Title-Case label. Failure falls through to `fallback_label` (typically
    the first slug, normalized) so downstream REDUCE always sees something."""
    chain = await _get_keylm_chain()
    user_msg = _KEYLM_USER_TEMPLATE.format(cluster_text=cluster_text[:LABEL_TEXT_CAP])
    async with semaphore:
        try:
            response = await chain.ainvoke(
                [
                    SystemMessage(content=_KEYLM_SYSTEM),
                    HumanMessage(content=user_msg),
                ],
                # max_tokens applied per-call so the chain factory can stay
                # generic (other future small-LM tasks may want longer outputs).
                max_tokens=KEYLM_MAX_TOKENS,
            )
            raw = getattr(response, "content", "") or ""
        except Exception as e:
            logger.warning(
                f"[classical-map] KeyLLM rotator call failed "
                f"({type(e).__name__}: {str(e)[:120]}); using fallback {fallback_label!r}"
            )
            return fallback_label
    return _sanitize_label(raw, fallback_label)


async def _label_all_clusters(
    cluster_results: list[dict],
    semaphore: Optional[asyncio.Semaphore] = None,
) -> list[list[str]]:
    """
    For every shard's communities, generate one label per community in parallel
    (capped by `semaphore` to avoid Xinference queue blow-up). Returns the same
    nested shape as `[shard_idx][community_idx] -> label`.

    Triggers exactly ONE model swap (embedding → instruct LM) on first call.
    """
    sem = semaphore or asyncio.Semaphore(KEYLM_CONCURRENCY)
    coros: list = []
    locator: list[tuple[int, int]] = []  # (shard_idx, comm_idx) for each coro

    for s_idx, cr in enumerate(cluster_results):
        slugs = cr["slugs"]
        snippets = cr["snippets"]
        for c_idx, members in enumerate(cr["communities"]):
            cluster_text = " ".join(snippets[i] for i in members)
            # Fallback label: first slug, normalized. Used when KeyLLM call fails.
            fallback = slugs[members[0]].replace("-", " ").title() if members else "Cluster"
            coros.append(_label_one_cluster(cluster_text, fallback, sem))
            locator.append((s_idx, c_idx))

    if not coros:
        return [[] for _ in cluster_results]

    flat_labels = await asyncio.gather(*coros)
    # Reshape back into per-shard
    out: list[list[str]] = [[None] * len(cr["communities"]) for cr in cluster_results]  # type: ignore[list-item]
    for (s_idx, c_idx), label in zip(locator, flat_labels):
        out[s_idx][c_idx] = label
    return out


# =============================================================================
# Public API — drop-in replacement for distiller.py's per-shard MAP gather
# =============================================================================
async def label_shards_classical(
    shards: list[list[tuple[str, str]]],
) -> list[ShardLabels]:
    """
    Two-phase classical MAP: embed + cluster ALL shards (Phase A) via the
    LiteLLM rotator's `kd-embed` group, then label ALL clusters across ALL
    shards (Phase B) via the rotator's `kd-keylm` group. Both phases are
    parallel; KEYLM_CONCURRENCY caps Phase B fanout to keep free-tier RPM
    safe. No local model hosting, no model swap.

    Args:
        shards: list of shards, each a list of (slug, content) tuples.

    Returns:
        list[ShardLabels] aligned 1:1 with `shards`. Schema is identical to
        the LLM path so distiller.py routing is a drop-in flag swap.
    """
    if not shards:
        return []

    # Phase A — all parallel, embedding model loaded
    t0 = time.monotonic()
    cluster_results = await _cluster_shards(shards)
    cluster_dt = time.monotonic() - t0

    # Phase B — all parallel, instruct model loaded (one swap on first call)
    t1 = time.monotonic()
    labels_per_shard = await _label_all_clusters(cluster_results)
    label_dt = time.monotonic() - t1

    # Build ShardLabels per shard
    output: list[ShardLabels] = []
    for s_idx, cr in enumerate(cluster_results):
        slugs = cr["slugs"]
        snippets = cr["snippets"]
        communities = cr["communities"]
        shard_labels = labels_per_shard[s_idx]

        clusters: list[ShardCluster] = []
        used_indices: set[int] = set()
        for c_idx, members in enumerate(communities):
            label = shard_labels[c_idx] or "Cluster"
            rep_slugs = [slugs[i] for i in members[:3]]
            description = (
                f"{len(members)} files clustered by Qwen3 cosine similarity. "
                f"Representatives: {', '.join(rep_slugs)}"
            )[:150]
            clusters.append(ShardCluster(
                cluster_name=label,
                description=description,
                file_slugs=[slugs[i] for i in members],
            ))
            used_indices.update(members)

        unused_slugs = [slugs[i] for i in range(len(slugs)) if i not in used_indices]

        # ShardLabels requires min_length=1 for `clusters`. If community_detection
        # found nothing (heterogeneous shard or N<2), emit a single fallback so
        # the schema validates — REDUCE re-clusters globally and absorbs it.
        if not clusters:
            clusters = [ShardCluster(
                cluster_name=f"Shard {s_idx + 1} (heterogeneous)",
                description=(
                    f"No cosine community ≥{COMMUNITY_THRESHOLD} found at "
                    f"min_size={MIN_COMMUNITY_SIZE}; forwarding all slugs for "
                    f"REDUCE to re-cluster globally."
                ),
                file_slugs=slugs,
            )]
            unused_slugs = []

        output.append(ShardLabels(clusters=clusters, unused_shard_slugs=unused_slugs))

    total = sum(len(s) for s in shards)
    n_clusters = sum(len(o.clusters) for o in output)
    logger.info(
        f"[classical-map] {len(shards)} shards / {total} files → "
        f"{n_clusters} clusters in {time.monotonic() - t0:.2f}s "
        f"(Phase A embed+cluster {cluster_dt:.2f}s; "
        f"Phase B KeyLLM label {label_dt:.2f}s)"
    )
    return output


# =============================================================================
# Legacy single-shard entry — kept for /debug/map_compare backward-compat.
# Production planner uses `label_shards_classical(shards)` (two-phase).
# =============================================================================
async def label_shard_classical(
    shard_entries: list[tuple[str, str]],
    shard_idx: int,
    n_shards: int,
) -> ShardLabels:
    """
    Single-shard wrapper around the two-phase pipeline. Useful for
    /debug/map_compare side-by-side per-shard inspection. **Triggers two
    model swaps per call** — do NOT use this in the hot path; planner
    routes to `label_shards_classical([s1, s2, ...])` instead.
    """
    results = await label_shards_classical([shard_entries])
    return results[0]
