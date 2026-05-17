"""Substep 2 — off_topic: semantic noise filter via cosine embeddings.

Embeds a framework-descriptor anchor (e.g. "Documentation for Pydantic, a
Python library for data validation using type hints") + every page body
in the corpus via NIM `nvidia/llama-nemotron-embed-1b-v2` through the
LLM rotator (`kd-embed` group). Drops pages whose cosine similarity to
the anchor falls below `_THRESHOLD` (0.30, v1 production-validated).

Stage 2 of the layered noise-filter pipeline. Stage 1 (URL path filter
at ingestion time) catches obvious patterns; this stage catches
semantic off-topic content that survived because its URL looked
innocent (e.g., `/docs/contributing-guide/`, `/docs/code-of-conduct/`,
or generated index pages with no real teaching content).

Inputs read from state:
  framework_slug — to look up the catalog entry (name, category, etc.)
  raw_files      — list of MinIO keys from corpus_load

Outputs written to state:
  relevant_files  — subset of raw_files with cosine >= threshold
  off_topic_stats — observability dict (kept, dropped, threshold,
                    domain_coherence, per_file_cosines, elapsed_ms)
"""
from __future__ import annotations

import logging
import math
import time

from routers.v1.docs_distiller.resolver import _index_by_slug
from services.docs_distiller.ingestion.storage_minio import get_storage
from services.llm.chain import embed_via_router_async

from ..observability.spans import traced
from ..state import PlannerState


logger = logging.getLogger(__name__)


_THRESHOLD = 0.30                # v1 production-validated cutoff
_MAX_BODY_CHARS = 8000           # truncate long bodies before embedding


def _cosine(a: list[float], b: list[float]) -> float:
    """Plain-Python cosine similarity. Numpy would be faster but for
    one-shot per-page computation the dot+sqrt overhead is negligible
    and we avoid the numpy import in the planner module."""
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (na * nb)


def _build_descriptor(entry: dict) -> str:
    """Anchor prompt for the framework. Uses the catalog name + category;
    extend with a `description:` catalog field later for richer anchors."""
    name = entry.get("name") or entry.get("slug") or "unknown"
    category = entry.get("category") or ""
    if category:
        return (
            f"Documentation for {name}, a {category} library / framework. "
            f"Teaching content: tutorials, guides, API reference, how-to "
            f"articles, conceptual explanations."
        )
    return (
        f"Documentation for {name}. Teaching content: tutorials, "
        f"guides, API reference, how-to articles, conceptual explanations."
    )


@traced("off_topic")
async def off_topic(state: PlannerState) -> dict:
    slug = state.get("framework_slug")
    raw_files = state.get("raw_files") or []
    if not slug or not raw_files:
        return {
            "relevant_files": list(raw_files),
            "off_topic_stats": {
                "kept": len(raw_files), "dropped": 0,
                "threshold": _THRESHOLD, "skipped": "no input",
            },
        }

    entry = _index_by_slug().get(slug, {})
    descriptor = _build_descriptor(entry)

    t0 = time.monotonic()
    minio = get_storage()

    # Parallel body read via the shared-client batched primitive we
    # ported from v1. Bodies stay in memory only for this node.
    bodies = await minio.read_many(raw_files)

    # Truncate to keep embed-batch payloads bounded. Documentation
    # tutorials are usually under 8K chars; long-form API references
    # get clipped (the first 8K chars are typically the API summary,
    # which is enough signal for topical similarity).
    truncated = [(b or "")[:_MAX_BODY_CHARS] for b in bodies]

    # Embed anchor + page bodies in one batched call. Rotator returns
    # vectors in the same order as input.
    vectors = await embed_via_router_async([descriptor] + truncated)
    anchor_vec = vectors[0]
    page_vecs = vectors[1:]

    threshold = _THRESHOLD
    relevant: list[str] = []
    per_file: list[tuple[str, float, bool]] = []
    cos_kept: list[float] = []
    for key, vec in zip(raw_files, page_vecs):
        c = _cosine(anchor_vec, vec)
        kept = c >= threshold
        # Store the leaf filename for the UI table — the full MinIO
        # key is too long to render usefully.
        leaf = key.rsplit("/", 1)[-1]
        per_file.append((leaf, round(c, 4), kept))
        if kept:
            relevant.append(key)
            cos_kept.append(c)

    domain_coherence = sum(cos_kept) / len(cos_kept) if cos_kept else 0.0
    elapsed_ms = int((time.monotonic() - t0) * 1000)

    stats = {
        "kept":             len(relevant),
        "dropped":          len(raw_files) - len(relevant),
        "threshold":        threshold,
        "domain_coherence": round(domain_coherence, 4),
        "per_file_cosines": per_file,
        "elapsed_ms":       elapsed_ms,
        "anchor_descriptor": descriptor,
    }

    # Attach summary numbers to the OTel span (full per-file list goes
    # in state, not the span — would bloat trace payload).
    try:
        from opentelemetry import trace as _otel_trace
        span = _otel_trace.get_current_span()
        span.set_attribute("off_topic.kept", stats["kept"])
        span.set_attribute("off_topic.dropped", stats["dropped"])
        span.set_attribute("off_topic.threshold", threshold)
        span.set_attribute("off_topic.domain_coherence", stats["domain_coherence"])
        span.set_attribute("off_topic.elapsed_ms", elapsed_ms)
    except Exception:
        pass

    logger.info(
        f"[off_topic] {slug}: kept {stats['kept']}/{len(raw_files)} "
        f"(dropped {stats['dropped']}), domain_coherence="
        f"{stats['domain_coherence']:.3f}, elapsed={elapsed_ms}ms"
    )
    return {"relevant_files": relevant, "off_topic_stats": stats}
