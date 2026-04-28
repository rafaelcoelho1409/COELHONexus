"""
Post-ingest corpus normalization.

Runs AFTER `ingest_framework_docs` completes and BEFORE the LangGraph
planner sees the corpus. Lives in the service layer (not the graph) so:
  - the cache stores the normalized shape (no re-splitting on every run)
  - normalization is reusable by /ingestion, /studies, future /ask, /export
  - it's testable without a graph fixture
  - tweaking the splitter doesn't require running the full graph

CURRENT STEP — split_monolith_if_needed:
  Tier 1 (`/llms-full.txt`) writes ONE monolithic .md file (often 1-10 MB).
  The downstream planner / synthesizer expects per-page granularity. This
  step splits the monolith on H1/H2 boundaries using LangChain's
  CommonMark-aware splitter (fence-respecting — a # comment INSIDE a
  ```python block is NOT treated as a heading), writes per-section .md
  files, and rewrites the manifest accordingly. Idempotent — a multi-page
  manifest passes through unchanged.
"""

from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING

from schemas.knowledge.ingestion import ManifestEntry

if TYPE_CHECKING:
    from services.knowledge.storage import MinIOStudyStorage

logger = logging.getLogger(__name__)


# If the raw prefix contains exactly ONE object larger than this, split it
# on top-level markdown headings before persisting the manifest. Mainly
# triggered by Tier 1 (single-file llms-full.txt fast path).
MONOLITH_SPLIT_THRESHOLD_BYTES = 50_000


async def split_monolith_if_needed(
    storage: "MinIOStudyStorage",
    study_root: str,
    manifest: list[ManifestEntry]) -> list[ManifestEntry]:
    """
    If `manifest` has exactly one entry whose body exceeds the size
    threshold, split it on H1/H2 boundaries using LangChain's
    CommonMark-aware splitter. Writes per-section .md files in MinIO,
    deletes the original monolith, and returns the post-split manifest.

    Idempotent. Pre-split or small-enough corpora pass through unchanged.

    The splitter is fence-aware: comment-line `#` chars inside fenced code
    blocks (```python `# 1. step`, ```bash `# echo X`) are NOT treated as
    headings. Earlier naive regex implementations corrupted ~13% of split
    sections by cutting mid-fence; the LangChain tokenizer fixes this.
    """
    if len(manifest) != 1:
        return manifest

    only = manifest[0]
    slug = only.slug
    src_key = f"{study_root}/research/raw/{slug}.md"
    try:
        content = await storage.read_text(src_key)
    except Exception as e:
        logger.warning(
            f"[post-ingest] cannot read {src_key} for split: {e}; "
            f"keeping monolith"
        )
        return manifest

    if len(content.encode("utf-8")) < MONOLITH_SPLIT_THRESHOLD_BYTES:
        return manifest

    # Local import — heavy dep chain; only loaded when actually splitting.
    from langchain_text_splitters.markdown import (
        ExperimentalMarkdownSyntaxTextSplitter,
    )

    splitter = ExperimentalMarkdownSyntaxTextSplitter(
        headers_to_split_on = [("#", "H1"), ("##", "H2")],
        strip_headers = False,
    )
    chunks = splitter.split_text(content)
    if len(chunks) < 3:
        logger.info(
            f"[post-ingest] monolith {slug}.md has too few headings to "
            f"split ({len(chunks)} chunks); keeping as-is"
        )
        return manifest

    # Group chunks by (H1, H2) so each output file is a coherent section
    # with code blocks landing back inside their surrounding section
    # (in document order).
    grouped: list[tuple[tuple[str, str], list[str]]] = []
    current_key: tuple[str, str] | None = None
    current_parts: list[str] = []
    for ch in chunks:
        key = (ch.metadata.get("H1", ""), ch.metadata.get("H2", ""))
        if current_key is None:
            current_key = key
        if key != current_key and current_parts:
            grouped.append((current_key, current_parts))
            current_parts = []
            current_key = key
        current_parts.append(ch.page_content)
    if current_parts and current_key is not None:
        grouped.append((current_key, current_parts))

    if len(grouped) < 3:
        logger.info(
            f"[post-ingest] monolith {slug}.md produced {len(grouped)} "
            f"sections (under minimum of 3); keeping as-is"
        )
        return manifest

    prefix = f"{study_root}/research/raw/"
    await storage.delete(src_key)

    # Phase 1 — slug + body for every section. Sequential pass: slug
    # dedup depends on order-of-arrival when two H2 "Overview" appear
    # under different H1s. CPU-only, no I/O.
    writes: list[tuple[str, str]] = []
    used_slugs: set[str] = set()
    for i, ((h1, h2), parts) in enumerate(grouped):
        heading_text = h2 or h1 or f"section-{i:04d}"
        sub = re.sub(r"[^a-z0-9]+", "-", heading_text.lower()).strip("-")[:60]
        if not sub:
            sub = f"section-{i:04d}"
        full_slug = sub if sub.startswith(slug) else f"{slug}-{sub}"
        candidate = full_slug
        dedup_n = 2
        while candidate in used_slugs:
            candidate = f"{full_slug}-{dedup_n}"
            dedup_n += 1
        used_slugs.add(candidate)
        writes.append((candidate, "".join(parts)))

    # Phase 2 — parallel MinIO writes via shared aioboto3 client. Avoids
    # per-call TLS+SigV4 handshake; measured ~40× speedup for ~3700-section
    # llms-full.txt files.
    await storage.write_many(
        [(f"{prefix}{candidate}.md", body, "text/markdown")
         for candidate, body in writes]
    )

    new_manifest = [
        ManifestEntry(
            url = only.url,        # all sections originated from the same fetch
            slug = candidate,
            tier = only.tier,
            bytes = len(body.encode("utf-8")),
        )
        for candidate, body in writes
    ]

    logger.info(
        f"[post-ingest] split monolith {slug}.md → {len(new_manifest)} "
        f"sections (CommonMark tokenizer; fences preserved; parallel writes)"
    )
    return new_manifest
