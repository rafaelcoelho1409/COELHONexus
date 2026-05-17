"""Post-ingest normalization.

Two responsibilities:

  1. Monolith split — when a tier produced one large markdown blob (mainly
     Tier 1's llms-full.txt), break it into per-original-page files. Two
     boundary strategies, tried in order:

       a) `Source: <url>` markers (modern llms-full.txt convention,
          e.g. Streamlit/Mintlify) — these are GROUND TRUTH original-page
          boundaries published by the upstream docs site. When present
          (≥3 markers), prefer them: each section starts at the H1 above
          the Source line and runs through to the next.
       b) H1 fallback — when no Source markers exist, split at H1
          boundaries only. H2/H3 are subsection structure, NOT page
          structure; splitting on them shatters originally-coherent
          pages into context-less fragments (the bug that gave Streamlit
          778 pages vs MLflow's 82 for similar-sized corpora).

     Both strategies use markdown-it-py's token stream so code fences,
     tables, HTML/MDX blocks are atomic — never bisected.

  2. Cleanup pass — drop sub-300 B stubs (was 64, too lenient; let
     micro-fragments through) and SHA256-dedupe byte-identical sections.
"""
import hashlib
import logging
import re

from .store import ManifestEntry, Store


logger = logging.getLogger(__name__)

MONOLITH_SPLIT_THRESHOLD_BYTES = 50_000
SPLIT_MIN_SECTION_BYTES = 300       # raised from 64; MLflow p10 is ~2.5 KB

# Matches: "Source: https://..." on its own line. The modern llms-full.txt
# convention (Mintlify, Streamlit, etc.) emits these immediately under each
# original page's H1 as the canonical original-page-boundary signal.
_SOURCE_LINE_RE = re.compile(
    r'^Source:\s+(https?://\S+)\s*$', re.MULTILINE,
)
_SOURCE_MIN_MARKERS = 3             # below this count, format isn't trustworthy


def _split_markdown_by_headings(
    text: str,
    split_levels: tuple[int, ...] = (1,),
) -> list[tuple[str, str, str]]:
    """Return [(h1, h2, body), ...] in document order.

    Code fences, tables, list blocks, and HTML blocks are atomic tokens —
    they cannot be bisected. Bodies are reconstructed by slicing the
    original text on the (start_line, end_line) ranges reported by
    markdown-it-py, so whitespace and fences round-trip exactly.
    """
    from markdown_it import MarkdownIt
    from mdit_py_plugins.deflist import deflist_plugin
    from mdit_py_plugins.footnote import footnote_plugin
    from mdit_py_plugins.front_matter import front_matter_plugin

    md = (
        MarkdownIt("commonmark", {"html": True})
        .enable(["table", "strikethrough"])
        .use(front_matter_plugin)
        .use(deflist_plugin)
        .use(footnote_plugin)
    )
    tokens = md.parse(text)
    lines = text.splitlines(keepends=True)

    boundaries: list[tuple[int, int, str]] = []
    for i, tok in enumerate(tokens):
        if tok.type != "heading_open" or tok.map is None:
            continue
        try:
            level = int(tok.tag[1])
        except (ValueError, IndexError):
            continue
        if level not in split_levels:
            continue
        heading_text = ""
        if i + 1 < len(tokens) and tokens[i + 1].type == "inline":
            heading_text = (tokens[i + 1].content or "").strip()
        boundaries.append((tok.map[0], level, heading_text))

    if not boundaries:
        return [("", "", text)] if text else []

    sections: list[tuple[str, str, str]] = []
    if boundaries[0][0] > 0:
        preamble = "".join(lines[:boundaries[0][0]])
        if preamble.strip():
            sections.append(("", "", preamble))

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


def _slugify_heading(s: str, fallback: str) -> str:
    s2 = re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")[:60]
    return s2 or fallback


def _split_by_source_markers(text: str) -> list[tuple[str, str, str]] | None:
    """Boundary strategy A: split at `Source: <url>` markers.

    For each Source line, walk back to the nearest preceding H1 — that's
    the section's true start (the page's title). Section body runs from
    that H1 through to the start of the next section.

    Returns [(h1_title, "", body), ...] in document order, OR None if
    the format doesn't carry enough Source markers to trust this strategy.
    """
    matches = list(_SOURCE_LINE_RE.finditer(text))
    if len(matches) < _SOURCE_MIN_MARKERS:
        return None

    lines = text.splitlines(keepends=True)
    # Build cumulative char offset per line for fast char→line lookup.
    line_offsets = [0]
    for line in lines:
        line_offsets.append(line_offsets[-1] + len(line))

    def _char_to_line_idx(pos: int) -> int:
        # Binary-search would be tidier; linear is plenty fast for our sizes.
        for i in range(len(line_offsets) - 1):
            if line_offsets[i] <= pos < line_offsets[i + 1]:
                return i
        return len(lines) - 1

    section_starts: list[tuple[int, str]] = []
    seen_starts: set[int] = set()
    for m in matches:
        src_line_idx = _char_to_line_idx(m.start())
        # Walk back to find the nearest H1 above this Source line. An H1
        # is a line that starts with "# " but NOT "## " or deeper.
        h1_idx = None
        h1_title = ""
        for cursor in range(src_line_idx - 1, -1, -1):
            ln = lines[cursor].rstrip("\n").rstrip("\r")
            if ln.startswith("# ") and not ln.startswith("## "):
                h1_idx = cursor
                h1_title = ln[2:].strip()
                break
        if h1_idx is None:
            # No H1 above → preamble; start at the Source line itself so
            # the section at least carries the URL marker.
            h1_idx = src_line_idx
            h1_title = ""
        if h1_idx in seen_starts:
            continue
        seen_starts.add(h1_idx)
        section_starts.append((h1_idx, h1_title))

    section_starts.sort(key=lambda s: s[0])

    sections: list[tuple[str, str, str]] = []
    # Capture any leading preamble before the first detected boundary.
    if section_starts and section_starts[0][0] > 0:
        preamble = "".join(lines[:section_starts[0][0]])
        if preamble.strip():
            sections.append(("", "", preamble))

    for j, (h1_idx, h1_title) in enumerate(section_starts):
        end = (
            section_starts[j + 1][0]
            if j + 1 < len(section_starts) else len(lines)
        )
        body = "".join(lines[h1_idx:end])
        sections.append((h1_title, "", body))
    return sections


def split_monolith(
    body: str,
    parent_slug: str,
) -> tuple[list[tuple[str, str]], int, int]:
    """Split a single large markdown body into (slug, section_body) pairs.

    Returns (writes, stubs_dropped, duplicates_dropped). Pure function —
    caller is responsible for persistence.

    Boundary strategy precedence:
      1. `Source: <url>` markers (true upstream page boundaries)
      2. H1 fallback (splits on H1 only, NOT H2 — H2 is subsection)

    Below MONOLITH_SPLIT_THRESHOLD_BYTES → returns the body unchanged as
    a single-entry list (idempotent for already-small or pre-split corpora).
    """
    if len(body.encode("utf-8")) < MONOLITH_SPLIT_THRESHOLD_BYTES:
        return [(parent_slug, body)], 0, 0

    sections = _split_by_source_markers(body)
    strategy = "source-markers"
    if sections is None:
        sections = _split_markdown_by_headings(body, split_levels=(1,))
        strategy = "h1-fallback"
    if len(sections) < 3:
        return [(parent_slug, body)], 0, 0

    logger.info(
        f"[post] split_monolith: {strategy} → {len(sections)} sections "
        f"(input {len(body)//1024} KB)"
    )

    width = max(4, len(str(max(0, len(sections) - 1))))
    writes: list[tuple[str, str]] = []
    for i, (h1, h2, sec_body) in enumerate(sections):
        # Prefer H1 (parent-page name) over H2 (subsection) — flipped from
        # the legacy ordering that buried parent context behind subsections.
        heading = h1 or h2 or f"section-{i:0{width}d}"
        sub = _slugify_heading(heading, f"section-{i:0{width}d}")
        base = sub if sub.startswith(parent_slug) else f"{parent_slug}-{sub}"
        # Slug is the heading-derived base only. The pre-cleanup section
        # index used to be prefixed here, but after stub-drop + dedup that
        # made the surviving slugs look "gappy" (0000, 0002, 0006…) — the
        # MinIO `page_key` already prepends a fresh contiguous `new_idx`
        # so the section-index here was both redundant and misleading.
        writes.append((base, sec_body))

    # Drop heading-only stubs.
    pre = len(writes)
    writes = [
        (s, b) for s, b in writes
        if len(b.encode("utf-8")) >= SPLIT_MIN_SECTION_BYTES
    ]
    stubs_dropped = pre - len(writes)

    # SHA256 content dedup. Keep first occurrence (lowest-ordinal slug) so
    # document order is preserved for downstream consumers.
    seen: set[str] = set()
    deduped: list[tuple[str, str]] = []
    duplicates_dropped = 0
    for s, b in writes:
        h = hashlib.sha256(b.encode("utf-8")).hexdigest()
        if h in seen:
            duplicates_dropped += 1
            continue
        seen.add(h)
        deduped.append((s, b))
    writes = deduped

    if stubs_dropped or duplicates_dropped:
        logger.info(
            f"[post] split cleanup: -{stubs_dropped} stubs, "
            f"-{duplicates_dropped} dupes; {len(writes)} survive (was {pre})"
        )
    return writes, stubs_dropped, duplicates_dropped


def dedup_pages(
    pages: list[tuple[str, str, str]],
) -> tuple[list[tuple[str, str, str]], int, int]:
    """Dedup a multi-page corpus (tier 2/3/4).

    Input: [(slug, url, body), ...]
    Output: (filtered, stubs_dropped, duplicates_dropped)

    Drops stubs and SHA256-dedupes. Same rules as the monolith split's
    cleanup phase — applied to the page set from multi-URL tiers.
    """
    pre = len(pages)
    kept = [
        (s, u, b) for s, u, b in pages
        if len(b.encode("utf-8")) >= SPLIT_MIN_SECTION_BYTES
    ]
    stubs_dropped = pre - len(kept)

    seen: set[str] = set()
    deduped: list[tuple[str, str, str]] = []
    duplicates_dropped = 0
    for s, u, b in kept:
        h = hashlib.sha256(b.encode("utf-8")).hexdigest()
        if h in seen:
            duplicates_dropped += 1
            continue
        seen.add(h)
        deduped.append((s, u, b))
    return deduped, stubs_dropped, duplicates_dropped


async def apply_to_store(store: Store) -> dict:
    """If the store holds exactly one large entry, split it. Otherwise dedup
    the page set. Rewrites the manifest + returns a summary dict suitable
    for `Progress.record_post`.

    Bodies live in MinIO — old objects are deleted by their MinIO key as
    the new layout is written; manifest is then atomically replaced.
    """
    from .storage_minio import page_key

    current = store.manifest
    input_files = len(current)
    input_bytes = sum(e.bytes for e in current)

    # Single-entry monolith path
    if input_files == 1 and current[0].bytes >= MONOLITH_SPLIT_THRESHOLD_BYTES:
        only = current[0]
        try:
            body = await store.read_body(0)
        except Exception as e:
            logger.warning(f"[post] body read failed: {e}")
            return _summary("split", input_files, input_bytes, current)

        writes, stubs, dupes = split_monolith(body, only.slug)
        if len(writes) == 1 and writes[0][1] == body:
            return _summary(
                "split", input_files, input_bytes, current, was_split=False,
            )

        # Wipe the old monolith body, write new sections in parallel.
        await store.delete_body_by_key(only.key)
        new_entries: list[ManifestEntry] = []
        write_batch: list = []
        for new_idx, (slug, sec_body) in enumerate(writes):
            new_key = page_key(store.framework_slug, new_idx, slug)
            write_batch.append((new_key, sec_body, "text/markdown"))
            new_entries.append(ManifestEntry(
                idx=new_idx, slug=slug, url=only.url, tier=only.tier,
                bytes=len(sec_body.encode("utf-8")),
                title=slug, key=new_key,
            ))
        await store.minio.write_many(write_batch)
        await store.replace_manifest(new_entries)
        return _summary(
            "split", input_files, input_bytes, new_entries,
            was_split=True, stubs=stubs, dupes=dupes,
        )

    # Multi-page path — dedup
    if input_files == 0:
        return _summary("dedup", 0, 0, [])

    # Parallel read every body — was serial @ ~50ms/key, so 1500 pages
    # took ~75s; bounded sem keeps from saturating the MinIO connection
    # pool while still amortizing the latency.
    import asyncio
    _read_sem = asyncio.BoundedSemaphore(32)

    async def _read_one(e):
        async with _read_sem:
            try:
                b = await store.read_body_by_key(e.key)
            except Exception:
                b = ""
        return (e.slug, e.url, b)

    raw_pages: list[tuple[str, str, str]] = list(
        await asyncio.gather(*(_read_one(e) for e in current))
    )

    deduped, stubs, dupes = dedup_pages(raw_pages)
    if stubs == 0 and dupes == 0:
        return _summary("dedup", input_files, input_bytes, current)

    # Parallel wipe of old MinIO objects (same rationale — was serial).
    _del_sem = asyncio.BoundedSemaphore(32)

    async def _del_one(e):
        async with _del_sem:
            await store.delete_body_by_key(e.key)

    await asyncio.gather(*(_del_one(e) for e in current))
    new_entries = []
    write_batch: list = []
    for new_idx, (slug, url, body) in enumerate(deduped):
        prev = next(
            (e for e in current if e.url == url and e.slug == slug),
            None,
        )
        tier = prev.tier if prev else (current[0].tier if current else "unknown")
        title = prev.title if prev else slug
        new_key = page_key(store.framework_slug, new_idx, slug)
        write_batch.append((new_key, body, "text/markdown"))
        new_entries.append(ManifestEntry(
            idx=new_idx, slug=slug, url=url, tier=tier,
            bytes=len(body.encode("utf-8")),
            title=title, key=new_key,
        ))
    await store.minio.write_many(write_batch)
    await store.replace_manifest(new_entries)
    return _summary(
        "dedup", input_files, input_bytes, new_entries,
        was_split=False, stubs=stubs, dupes=dupes,
    )


def _summary(
    kind: str,
    input_files: int,
    input_bytes: int,
    out_entries: list[ManifestEntry],
    *,
    was_split: bool = False,
    stubs: int = 0,
    dupes: int = 0,
) -> dict:
    return {
        "kind": kind,
        "input_files": input_files,
        "input_bytes": input_bytes,
        "output_files": len(out_entries),
        "output_bytes": sum(e.bytes for e in out_entries),
        "was_split": was_split,
        "stubs_dropped": stubs,
        "duplicates_dropped": dupes,
    }
