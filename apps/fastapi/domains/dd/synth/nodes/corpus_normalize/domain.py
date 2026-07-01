"""corpus_normalize — 8-pass markdown cleanup pipeline; pure + idempotent."""
from __future__ import annotations

import unicodedata
from typing import Optional

from .params import ENTITY_DECODES
from .patterns import (
    ADMON_CLOSE_RE,
    ADMON_OPEN_RE,
    BOUNDARY_RE,
    FRONTMATTER_RE,
    GITBOOK_HINT_CLOSE_RE,
    GITBOOK_HINT_OPEN_RE,
    GITBOOK_TABS_CLOSE_RE,
    GITBOOK_TABS_OPEN_RE,
    MDX_CLOSE_TAG_RE,
    MDX_OPEN_TAG_RE,
    ZERO_WIDTH_RE,
)
from .schemas import NormalizedDoc, NormalizeStats
from .versions import NORMALIZER_VERSION


def normalize_doc(
    md_text: str,
    *,
    source_url: Optional[str] = None,
) -> NormalizedDoc:
    """Pure normalization pipeline. Idempotent. No I/O."""
    stats = NormalizeStats(input_bytes = len(md_text.encode("utf-8")))
    text = md_text

    text, n_zw = _unicode_pass(text)
    stats.zero_width_chars_stripped = n_zw

    text, frontmatter = _frontmatter_pass(text)
    stats.frontmatter_extracted = bool(frontmatter)

    text, n_bound = _boundary_pass(text)
    stats.boundary_markers_stripped = n_bound

    text, n_meta, n_admon, n_orphan = _token_aware_passes(text)
    stats.fence_meta_stripped       = n_meta
    stats.container_admonitions     = n_admon
    stats.orphan_tags_stripped      = n_orphan

    text, n_ent, n_blank, n_trail = _whitespace_entity_pass(text)
    stats.html_entities_decoded = n_ent
    stats.blank_lines_collapsed = n_blank
    stats.trailing_ws_lines     = n_trail

    stats.output_bytes = len(text.encode("utf-8"))

    return NormalizedDoc(
        body = text, frontmatter = frontmatter,
        stats = stats, version = NORMALIZER_VERSION,
    )


def _unicode_pass(text: str) -> tuple[str, int]:
    """NFC + BOM/NBSP/zero-width strip so downstream regexes see canonical codepoints."""
    n = 0
    if text.startswith("﻿"):
        text = text[1:]
    text = text.replace(" ", " ")
    n = len(ZERO_WIDTH_RE.findall(text))
    if n:
        text = ZERO_WIDTH_RE.sub("", text)
    text = unicodedata.normalize("NFC", text)
    return text, n


def _frontmatter_pass(text: str) -> tuple[str, dict]:
    """Extract YAML frontmatter → (body, dict); naive key:val parse to avoid yaml dep."""
    m = FRONTMATTER_RE.match(text)
    if not m:
        return text, {}
    raw = m.group("body")
    rest = text[m.end():]
    fm: dict = {}
    for line in raw.splitlines():
        line = line.rstrip()
        if not line or line.startswith("#"):
            continue
        if ":" not in line:
            continue
        k, _, v = line.partition(":")
        key = k.strip()
        val = v.strip().strip('"').strip("'")
        if key:
            fm[key] = val
    if not fm:
        fm = {"_raw": raw}
    return rest, fm


def _boundary_pass(text: str) -> tuple[str, int]:
    matches = BOUNDARY_RE.findall(text)
    if not matches:
        return text, 0
    return BOUNDARY_RE.sub("", text), len(matches)


def _identify_fence_ranges(text: str) -> list[tuple[int, int, int]]:
    """Return (open_line_idx, close_exclusive, kind) tuples; kind=0 backtick, 1 tilde."""
    from markdown_it import MarkdownIt
    md = MarkdownIt("commonmark")
    ranges: list[tuple[int, int, int]] = []
    for tok in md.parse(text):
        if tok.type != "fence" or tok.map is None:
            continue
        start, end = tok.map
        kind = 1 if (tok.markup and tok.markup[0] == "~") else 0
        ranges.append((start, end, kind))
    ranges.sort()
    return ranges


def _strip_fence_info_string(info: str) -> tuple[str, bool]:
    """Reduce fence info-string to first token (lang), stripping Mintlify attrs."""
    info = info.strip()
    if not info:
        return "", False
    parts = info.split(None, 1)
    if len(parts) == 1:
        return parts[0], False
    return parts[0], True


def _token_aware_passes(text: str) -> tuple[str, int, int, int]:
    """Fence-range-aware line walk: rewrite openers, skip bodies, strip prose tags."""
    fence_ranges = _identify_fence_ranges(text)
    lines = text.split("\n")

    fence_body_lines: set[int] = set()
    fence_opener_lines: dict[int, int] = {}     # line_idx → kind
    for (open_idx, close_idx, kind) in fence_ranges:
        fence_opener_lines[open_idx] = kind
        for i in range(open_idx + 1, close_idx):
            fence_body_lines.add(i)

    n_meta   = 0
    n_admon  = 0
    n_orphan = 0

    out_lines: list[str] = []
    for i, line in enumerate(lines):
        if i in fence_body_lines:
            out_lines.append(line)
            continue

        if i in fence_opener_lines:
            kind = fence_opener_lines[i]
            new_line, stripped = _rewrite_fence_opener(line, kind)
            if stripped:
                n_meta += 1
            out_lines.append(new_line)
            continue

        new_line, was_admon = _strip_admonition_markers(line)
        if was_admon:
            n_admon += 1
        new_line, n_o = _strip_mdx_wrapper_tags(new_line)
        n_orphan += n_o
        out_lines.append(new_line)

    return "\n".join(out_lines), n_meta, n_admon, n_orphan


def _rewrite_fence_opener(line: str, kind: int) -> tuple[str, bool]:
    """Strip Mintlify attrs from fence opener line."""
    fence_char = "~" if kind else "`"
    stripped = line.lstrip()
    indent = line[: len(line) - len(stripped)]
    if not stripped.startswith(fence_char * 3):
        return line, False
    n_chars = 0
    for ch in stripped:
        if ch == fence_char:
            n_chars += 1
        else:
            break
    fence_markers = fence_char * n_chars
    info = stripped[n_chars:]
    new_info, was_changed = _strip_fence_info_string(info)
    if not was_changed:
        return line, False
    new_line = (
        indent + fence_markers + (new_info if new_info else "")
    )
    return new_line.rstrip(), True


def _strip_admonition_markers(line: str) -> tuple[str, bool]:
    """Strip :::admonition and GitBook {% hint %} delimiter lines; inner text preserved."""
    for pattern in (
        ADMON_OPEN_RE, ADMON_CLOSE_RE,
        GITBOOK_HINT_OPEN_RE, GITBOOK_HINT_CLOSE_RE,
        GITBOOK_TABS_OPEN_RE, GITBOOK_TABS_CLOSE_RE,
    ):
        if pattern.fullmatch(line):
            return "", True
    return line, False


def _strip_mdx_wrapper_tags(line: str) -> tuple[str, int]:
    """Strip MDX/JSX wrapper tags from a prose line; preserves inline-code spans."""
    if "`" not in line:
        # Fast path — no inline code possible, apply regex directly.
        new_line, n_o = MDX_OPEN_TAG_RE.subn("", line)
        new_line, n_c = MDX_CLOSE_TAG_RE.subn("", new_line)
        return new_line, n_o + n_c

    parts: list[tuple[str, bool]] = []   # (segment, is_code)
    i = 0
    while i < len(line):
        if line[i] == "`":
            j = i
            while j < len(line) and line[j] == "`":
                j += 1
            tick_len = j - i
            delim = "`" * tick_len
            close_at = line.find(delim, j)
            if close_at == -1:
                parts.append((line[i:], False))
                i = len(line)
                continue
            parts.append((line[i:close_at + tick_len], True))
            i = close_at + tick_len
        else:
            j = i
            while j < len(line) and line[j] != "`":
                j += 1
            parts.append((line[i:j], False))
            i = j

    n_total = 0
    rebuilt: list[str] = []
    for seg, is_code in parts:
        if is_code:
            rebuilt.append(seg)
            continue
        seg, n_o = MDX_OPEN_TAG_RE.subn("", seg)
        seg, n_c = MDX_CLOSE_TAG_RE.subn("", seg)
        n_total += n_o + n_c
        rebuilt.append(seg)
    return "".join(rebuilt), n_total


def _whitespace_entity_pass(text: str) -> tuple[str, int, int, int]:
    """Final cosmetic pass: decode whitelisted HTML entities, collapse
    runs of ≥3 blank lines to a single blank, strip per-line trailing
    whitespace."""
    n_ent = 0
    for src, dst in ENTITY_DECODES:
        c = text.count(src)
        if c:
            text = text.replace(src, dst)
            n_ent += c

    fence_ranges = _identify_fence_ranges(text)
    fence_body_lines: set[int] = set()
    for (open_idx, close_idx, _) in fence_ranges:
        for i in range(open_idx + 1, close_idx):
            fence_body_lines.add(i)

    lines = text.split("\n")
    out: list[str] = []
    n_trail = 0
    n_blank = 0
    blank_streak = 0
    for i, line in enumerate(lines):
        if i in fence_body_lines:
            out.append(line)
            blank_streak = 0
            continue
        if line != line.rstrip():
            n_trail += 1
            line = line.rstrip()
        if line == "":
            blank_streak += 1
            if blank_streak >= 3:
                n_blank += 1
                continue
        else:
            blank_streak = 0
        out.append(line)

    return "\n".join(out), n_ent, n_blank, n_trail
