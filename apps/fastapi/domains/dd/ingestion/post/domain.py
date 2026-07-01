"""Monolith splitter: Source-marker → H1 → H2/H3 precedence (Docusaurus: single H1 wraps the whole bundle). markdown-it tokens keep code fences/tables/HTML atomic."""
from __future__ import annotations

import hashlib
import logging
import re

from markdown_it import MarkdownIt
from mdit_py_plugins.deflist import deflist_plugin
from mdit_py_plugins.footnote import footnote_plugin
from mdit_py_plugins.front_matter import front_matter_plugin

from .params import (
    MONOLITH_SPLIT_THRESHOLD_BYTES,
    SOURCE_MIN_MARKERS,
    SPLIT_MAX_SECTION_BYTES,
    SPLIT_MIN_SECTION_BYTES,
)
from .patterns import H1_PREFIX_RE, SOURCE_LINE_RE


logger = logging.getLogger(__name__)


def split_markdown_by_headings(
    text: str,
    split_levels: tuple[int, ...] = (1,),
) -> list[tuple[str, str, str]]:
    """[(h1, h2, body), ...] in doc order. Driven by markdown-it tokens so
    code fences/tables/HTML stay atomic."""
    md = (
        MarkdownIt("commonmark", {"html": True})
        .enable(["table", "strikethrough"])
        .use(front_matter_plugin)
        .use(deflist_plugin)
        .use(footnote_plugin)
    )
    tokens = md.parse(text)
    lines = text.splitlines(keepends = True)
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


def slugify_heading(s: str, fallback: str) -> str:
    s2 = re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")[:60]
    return s2 or fallback


def split_by_source_markers(text: str) -> list[tuple[str, str, str]] | None:
    """Boundary strategy A: walk back from each `Source: <url>` to the nearest
    H1 — that's the section start. None if < SOURCE_MIN_MARKERS markers."""
    matches = list(SOURCE_LINE_RE.finditer(text))
    if len(matches) < SOURCE_MIN_MARKERS:
        return None
    lines = text.splitlines(keepends = True)
    line_offsets = [0]
    for line in lines:
        line_offsets.append(line_offsets[-1] + len(line))
    def _char_to_line_idx(pos: int) -> int:
        for i in range(len(line_offsets) - 1):
            if line_offsets[i] <= pos < line_offsets[i + 1]:
                return i
        return len(lines) - 1
    section_starts: list[tuple[int, str]] = []
    seen_starts: set[int] = set()
    for m in matches:
        src_line_idx = _char_to_line_idx(m.start())
        h1_idx = None
        h1_title = ""
        for cursor in range(src_line_idx - 1, -1, -1):
            ln = lines[cursor].rstrip("\n").rstrip("\r")
            if ln.startswith("# ") and not ln.startswith("## "):
                h1_idx = cursor
                h1_title = ln[2:].strip()
                break
        if h1_idx is None:
            # No H1 above — start at Source line to preserve the URL marker.
            h1_idx = src_line_idx
            h1_title = ""
        if h1_idx in seen_starts:
            continue
        seen_starts.add(h1_idx)
        section_starts.append((h1_idx, h1_title))
    section_starts.sort(key = lambda s: s[0])
    sections: list[tuple[str, str, str]] = []
    if section_starts and section_starts[0][0] > 0:
        preamble = "".join(lines[:section_starts[0][0]])
        if preamble.strip():
            sections.append(("", "", preamble))
    for j, (h1_idx, h1_title) in enumerate(section_starts):
        end = (section_starts[j + 1][0]
               if j + 1 < len(section_starts) else len(lines))
        body = "".join(lines[h1_idx:end])
        sections.append((h1_title, "", body))
    return sections


def split_monolith(
    body: str,
    parent_slug: str,
) -> tuple[list[tuple[str, str]], int, int]:
    """→ (writes, stubs_dropped, dupes_dropped). Pure — caller persists.
    Below MONOLITH_SPLIT_THRESHOLD_BYTES returns body unchanged."""
    if len(body.encode("utf-8")) < MONOLITH_SPLIT_THRESHOLD_BYTES:
        return [(parent_slug, body)], 0, 0
    sections = split_by_source_markers(body)
    strategy = "source-markers"
    if sections is None:
        sections = split_markdown_by_headings(body, split_levels = (1,))
        strategy = "h1-fallback"
    if len(sections) < 3:
        # Docusaurus single-H1 case — fall through to H2/H3.
        logger.info(
            f"[post] split_monolith: {strategy} → only {len(sections)} "
            f"H1 section(s) (input {len(body)//1024} KB); falling through "
            f"to size-aware H2/H3 sub-split"
        )
        expanded = _h2_subsplit_oversized([(parent_slug, body)], parent_slug)
        if len(expanded) <= 1:
            return [(parent_slug, body)], 0, 0
        # Cross-level dedup — recursion can split footers into duplicates.
        seen: set[str] = set()
        deduped: list[tuple[str, str]] = []
        dropped = 0
        for s, b in expanded:
            h = hashlib.sha256(b.encode("utf-8")).hexdigest()
            if h in seen:
                dropped += 1
                continue
            seen.add(h)
            deduped.append((s, b))
        if dropped:
            logger.info(
                f"[post] fall-through dedup: -{dropped} dupes; "
                f"{len(deduped)} survive (was {len(expanded)})"
            )
        return deduped, 0, dropped
    logger.info(
        f"[post] split_monolith: {strategy} → {len(sections)} sections "
        f"(input {len(body)//1024} KB)"
    )
    width = max(4, len(str(max(0, len(sections) - 1))))
    writes: list[tuple[str, str]] = []
    for i, (h1, h2, sec_body) in enumerate(sections):
        # H1 (parent) wins over H2 to preserve parent context.
        heading = h1 or h2 or f"section-{i:0{width}d}"
        sub = slugify_heading(heading, f"section-{i:0{width}d}")
        base = sub if sub.startswith(parent_slug) else f"{parent_slug}-{sub}"
        writes.append((base, sec_body))
    pre = len(writes)
    writes = [
        (s, b) for s, b in writes
        if len(b.encode("utf-8")) >= SPLIT_MIN_SECTION_BYTES
    ]
    stubs_dropped = pre - len(writes)
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
    # Oversized sections with <2 viable sub-pages stay intact (Dask numpy compat).
    writes = _h2_subsplit_oversized(writes, parent_slug)
    return writes, stubs_dropped, duplicates_dropped


def _h2_subsplit_oversized(
    writes: list[tuple[str, str]],
    parent_slug: str,
) -> list[tuple[str, str]]:
    """Recursive H2→H3 sub-split of oversized sections; parent `# Title`
    prepended so sub-pages remain self-contained."""
    expanded: list[tuple[str, str]] = []
    n_expanded = 0
    n_added_subsections = 0
    for s, b in writes:
        h1_match = H1_PREFIX_RE.match(b)
        h1_prefix = (h1_match.group(1) + "\n\n") if h1_match else ""
        sub_pages = _size_aware_recursive_split(
            s, b, h1_prefix = h1_prefix, levels = (2, 3),
        )
        if len(sub_pages) > 1:
            expanded.extend(sub_pages)
            n_expanded += 1
            n_added_subsections += len(sub_pages)
        else:
            expanded.append((s, b))
    if n_expanded:
        delta = n_added_subsections - n_expanded
        logger.info(
            f"[post] h2-subsplit: {n_expanded} oversized section(s) "
            f"(> {SPLIT_MAX_SECTION_BYTES // 1024} KB) expanded into "
            f"{n_added_subsections} sub-pages (+{delta} net entries)"
        )
    return expanded


def _size_aware_recursive_split(
    slug: str,
    body: str,
    h1_prefix: str,
    levels: tuple[int, ...],
) -> list[tuple[str, str]]:
    """Sub-split only over the size cap; falls through on ORIGINAL body
    (not on stub-dropped fragments)."""
    if len(body.encode("utf-8")) <= SPLIT_MAX_SECTION_BYTES:
        return [(slug, body)]
    if not levels:
        return [(slug, body)]
    level = levels[0]
    rest_levels = levels[1:]
    sub_sections = split_markdown_by_headings(body, split_levels = (level,))
    sub_writes: list[tuple[str, str]] = []
    for sub_h1, sub_h2, sub_b in sub_sections:
        heading = sub_h2 if sub_h2 else sub_h1
        if not heading:
            continue
        body_with_context = (
            h1_prefix + sub_b
            if h1_prefix and not sub_b.startswith(h1_prefix)
            else sub_b
        )
        sub_slug_part = slugify_heading(heading, "sub")
        new_slug = (sub_slug_part if sub_slug_part.startswith(slug)
                    else f"{slug}-{sub_slug_part}")
        sub_writes.append((new_slug, body_with_context))
    sub_writes = [
        (ss, sb) for ss, sb in sub_writes
        if len(sb.encode("utf-8")) >= SPLIT_MIN_SECTION_BYTES
    ]
    if len(sub_writes) < 2:
        return _size_aware_recursive_split(slug, body, h1_prefix, rest_levels)
    out: list[tuple[str, str]] = []
    for ss, sb in sub_writes:
        out.extend(_size_aware_recursive_split(ss, sb, h1_prefix, rest_levels))
    return out


def dedup_pages(
    pages: list[tuple[str, str, str]],
) -> tuple[list[tuple[str, str, str]], int, int]:
    """[(slug, url, body), ...] → (filtered, stubs, dupes). Same rules as
    the monolith split's cleanup."""
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


def make_summary(
    kind: str,
    input_files: int,
    input_bytes: int,
    output_entries: list,
    *,
    was_split: bool = False,
    stubs: int = 0,
    dupes: int = 0,
) -> dict:
    return {
        "kind":               kind,
        "input_files":        input_files,
        "input_bytes":        input_bytes,
        "output_files":       len(output_entries),
        "output_bytes":       sum(e.bytes for e in output_entries),
        "was_split":          was_split,
        "stubs_dropped":      stubs,
        "duplicates_dropped": dupes,
    }
