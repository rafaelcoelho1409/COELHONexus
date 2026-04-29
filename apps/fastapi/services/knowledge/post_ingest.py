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
  step splits the monolith on H1/H2 boundaries using `markdown-it-py`'s
  token stream — code fences (```...```), tables, HTML blocks, and other
  compound elements are emitted as ATOMIC tokens, so the splitter can
  never bisect them. Page bodies are reconstructed by slicing the original
  text on token-reported line ranges, preserving exact whitespace.

  Idempotent — a multi-page manifest passes through unchanged.

  Replaces the previous LangChain `ExperimentalMarkdownSyntaxTextSplitter`
  (regex-heuristic, would corrupt ~13% of sections by mishandling MDX,
  frontmatter, and mixed-fence styles).
"""

from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING

from schemas.knowledge.ingestion import ManifestEntry

if TYPE_CHECKING:
    from services.knowledge.storage import MinIOStudyStorage

logger = logging.getLogger(__name__)


# =============================================================================
# Token-aware markdown splitter (markdown-it-py)
# =============================================================================
# Atomic tokens produced by markdown-it-py guarantee that walking
# heading_open tokens never cuts through a code fence / table / HTML block.
# Section bodies are reconstructed by slicing the original text on the
# (start_line, end_line) ranges reported in `Token.map`, so whitespace,
# fences, frontmatter, and trailing newlines all round-trip exactly.

def _split_markdown_by_headings(
    text: str,
    split_levels: tuple[int, ...] = (1, 2),
) -> list[tuple[str, str, str]]:
    """
    Returns ``[(h1, h2, body), ...]`` ordered as encountered.

    - ``h1`` is the most recent H1 in scope; ``""`` for content before the
      first H1.
    - ``h2`` is the H2 heading text; ``""`` for sections that are H1-only or
      are the document preamble.
    - ``body`` is the literal source slice (newline-preserving), starting
      at the section's heading line and ending one line before the next
      split-level heading (or at EOF).
    - Document preamble (any content before the first split-level heading)
      is emitted as a single ``("", "", preamble)`` tuple if non-empty.

    Code fences, tables, lists, and HTML blocks are atomic markdown-it-py
    tokens — they cannot be bisected by this routine.
    """
    # Local imports — light deps, but only needed when we actually split.
    # Keeps cold-start cheap and the extension stack swap-able per-call.
    from markdown_it import MarkdownIt
    from mdit_py_plugins.deflist import deflist_plugin
    from mdit_py_plugins.footnote import footnote_plugin
    from mdit_py_plugins.front_matter import front_matter_plugin

    # Optimal preset for llms-full.txt processing:
    # - "commonmark" (NOT "gfm-like") — gfm-like enables linkify which needs
    #   linkify-it-py as an extra dep and doesn't help splitting
    # - html=True — MDX components (<ParamField>, <CodeGroup>, <Note>, ...)
    #   that Mintlify-generated llms-full.txt embeds become atomic html_block
    #   tokens; without this they parse as inline text and headings inside
    #   them could trip our splitter
    # - enable("table") — table rows atomic; never split mid-row
    # - enable("strikethrough") — GFM ~~text~~; harmless free win
    # - front_matter_plugin — YAML --- ... --- header recognized as atomic
    # - deflist_plugin — definition lists (`Term\n: Definition`) atomic
    # - footnote_plugin — footnote definitions atomic; keeps citations whole
    md = (
        MarkdownIt("commonmark", {"html": True})
        .enable(["table", "strikethrough"])
        .use(front_matter_plugin)
        .use(deflist_plugin)
        .use(footnote_plugin)
    )
    tokens = md.parse(text)
    lines = text.splitlines(keepends = True)

    boundaries: list[tuple[int, int, str]] = []  # (line_index, level, heading_text)
    for i, tok in enumerate(tokens):
        if tok.type != "heading_open" or tok.map is None:
            continue
        # tok.tag is "h1", "h2", ... → numeric level
        try:
            level = int(tok.tag[1])
        except (ValueError, IndexError):
            continue
        if level not in split_levels:
            continue
        # The companion `inline` token (next in the stream) carries the
        # rendered heading text; markdown-it always pairs them this way.
        heading_text = ""
        if i + 1 < len(tokens) and tokens[i + 1].type == "inline":
            heading_text = (tokens[i + 1].content or "").strip()
        boundaries.append((tok.map[0], level, heading_text))

    sections: list[tuple[str, str, str]] = []

    # Preamble — content before the first split-level heading.
    if not boundaries:
        return [("", "", text)] if text else []
    first_start = boundaries[0][0]
    if first_start > 0:
        preamble = "".join(lines[:first_start])
        if preamble.strip():
            sections.append(("", "", preamble))

    # Slice each heading → next-heading range.
    current_h1 = ""
    for j, (start, level, heading_text) in enumerate(boundaries):
        end = boundaries[j + 1][0] if j + 1 < len(boundaries) else len(lines)
        body = "".join(lines[start:end])
        if level == 1:
            current_h1 = heading_text
            sections.append((heading_text, "", body))
        else:
            sections.append((current_h1, heading_text, body))

    return sections


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
    threshold, split it on H1/H2 boundaries using markdown-it-py's token
    stream. Writes per-section .md files in MinIO, deletes the original
    monolith, and returns the post-split manifest.

    Idempotent. Pre-split or small-enough corpora pass through unchanged.

    Token-aware splitting guarantees code fences (```python / ```bash /
    etc.), tables, list blocks, and HTML blocks are NEVER bisected — they
    are emitted as atomic tokens by markdown-it-py and the slicer only
    cuts on `heading_open` boundaries. A `#` line *inside* a fenced code
    block is part of the fence's content stream, not a new section.
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

    sections = _split_markdown_by_headings(content, split_levels = (1, 2))
    if len(sections) < 3:
        logger.info(
            f"[post-ingest] monolith {slug}.md produced {len(sections)} "
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
    for i, (h1, h2, body) in enumerate(sections):
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
        writes.append((candidate, body))

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
        f"sections (markdown-it-py token stream; fences/tables atomic)"
    )
    return new_manifest
