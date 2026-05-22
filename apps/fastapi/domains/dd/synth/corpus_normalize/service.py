"""corpus_normalize — ingestion-time markdown cleanup.

Strips rendering noise (Mintlify fence attrs, MDX wrapper tags,
raw-corpus boundaries, Docusaurus/VitePress container admonitions,
GitBook hint blocks, GFM alerts that survived HTML->markdown, zero-
width chars, HTML entities) from ingested framework docs BEFORE any
downstream consumer (file viewer, embed_corpus, off_topic, cluster,
synth) sees the bytes.

Replaces the deprecated post-hoc 9-pass `_scrub_assembled_markdown`
passes 0-2 by moving the cleanup to INPUT-PREP. See:

  - docs/SYNTH-ARCHITECTURE-SOTA-2026-05-18.md (synth step 2)
  - May-2026 SOTA research transcript: MDKEYCHUNKER (arXiv 2603.23533)
    + Markdown-First Semantics (Steakhouse 2026) + MARCUS (2506.07116)

Pure functions, no I/O. Idempotent: normalize(normalize(x)) == normalize(x).

Pipeline composition:

    raw_doc
      | corpus_normalize (this module)
    cleaned_doc
      | vault_sentinelize (synth/vault.py)
    sentinelized_doc + vault

Pass order (single token-aware walk; each pass is line-local within
its responsibility -- fence bodies are never touched):

    1. Unicode/BOM normalize (NFC, strip zero-width chars)
    2. Extract YAML frontmatter -> metadata dict; strip from body
    3. Strip raw-corpus boundary markers (`--- foo.md ---`)
    4. Tokenize via markdown-it-py -> identify fence line ranges
    5. Rewrite fence info-strings (strip Mintlify attrs, keep lang)
    6. Strip container-admonition wrappers (:::tip, {% hint %})
    7. Strip MDX wrapper tags (<Tip>, <Tabs>, ...) -- text preserved
    8. Whitespace + entity hygiene (decode &amp;, collapse blanks)
"""
from __future__ import annotations

import unicodedata
from typing import Optional

from .constants import (
    _NORMALIZER_VERSION,
    _MDX_OPEN_TAG_RE,
    _MDX_CLOSE_TAG_RE,
    _FENCE_META_HINT_RE,
    _BOUNDARY_RE,
    _FRONTMATTER_RE,
    _ADMON_OPEN_RE,
    _ADMON_CLOSE_RE,
    _GITBOOK_HINT_OPEN_RE,
    _GITBOOK_HINT_CLOSE_RE,
    _GITBOOK_TABS_OPEN_RE,
    _GITBOOK_TABS_CLOSE_RE,
    _ZERO_WIDTH_RE,
    _ENTITY_DECODES,
)
from .types import NormalizeStats, NormalizedDoc


# ── Public API ────────────────────────────────────────────────────────

def normalize_doc(
    md_text: str,
    *,
    source_url: Optional[str] = None,
) -> NormalizedDoc:
    """Pure normalization pipeline. Idempotent. No I/O.

    Args:
        md_text: source markdown (UTF-8 string).
        source_url: optional URL for trace context; not used in v1
            but plumbed so callers can pass it for future analytics.

    Returns:
        NormalizedDoc with `body` (normalized markdown), `frontmatter`
        (extracted dict, may be empty), and `stats` (per-pass counts).
    """
    stats = NormalizeStats(input_bytes=len(md_text.encode("utf-8")))
    text = md_text

    # Pass 1 — Unicode + BOM + zero-width stripping
    text, n_zw = _unicode_pass(text)
    stats.zero_width_chars_stripped = n_zw

    # Pass 2 — extract YAML frontmatter (top-of-file only)
    text, frontmatter = _frontmatter_pass(text)
    stats.frontmatter_extracted = bool(frontmatter)

    # Pass 3 — strip raw-corpus boundary markers (line-anchored regex)
    text, n_bound = _boundary_pass(text)
    stats.boundary_markers_stripped = n_bound

    # Passes 4-7 — token-aware: identify fence regions; transforms apply
    # ONLY to prose lines (lines outside fence body ranges) for the
    # admonition + orphan-tag passes. Fence opener lines get their
    # info-string rewritten in-place. Fence bodies are pass-through.
    text, n_meta, n_admon, n_orphan = _token_aware_passes(text)
    stats.fence_meta_stripped       = n_meta
    stats.container_admonitions     = n_admon
    stats.orphan_tags_stripped      = n_orphan

    # Pass 8 — final cosmetic hygiene (whole-file)
    text, n_ent, n_blank, n_trail = _whitespace_entity_pass(text)
    stats.html_entities_decoded = n_ent
    stats.blank_lines_collapsed = n_blank
    stats.trailing_ws_lines     = n_trail

    stats.output_bytes = len(text.encode("utf-8"))

    return NormalizedDoc(
        body=text, frontmatter=frontmatter,
        stats=stats, version=_NORMALIZER_VERSION,
    )


# ── Internal passes ───────────────────────────────────────────────────

def _unicode_pass(text: str) -> tuple[str, int]:
    """Strip BOM, zero-width chars, normalize NBSP -> space. Apply
    NFC Unicode normalization so all downstream regexes see canonical
    codepoints."""
    n = 0
    # Strip BOM if at start (don't count toward zero-width tally).
    if text.startswith("﻿"):
        text = text[1:]
    # NBSP -> regular space (preserve token count).
    text = text.replace(" ", " ")
    # Count + strip other zero-width / formatting chars.
    n = len(_ZERO_WIDTH_RE.findall(text))
    if n:
        text = _ZERO_WIDTH_RE.sub("", text)
    # NFC normalize (precomposes diacritics) — safe for ASCII-heavy docs.
    text = unicodedata.normalize("NFC", text)
    return text, n


def _frontmatter_pass(text: str) -> tuple[str, dict]:
    """Extract YAML frontmatter from top of file, return (body, dict).
    Uses naive YAML parse (key: value) so we don't add a yaml dep just
    for this. Frontmatter that's too complex falls back to a single
    `_raw` string field — the synth pipeline doesn't need anything
    beyond `title` + `description` for v1."""
    m = _FRONTMATTER_RE.match(text)
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
    matches = _BOUNDARY_RE.findall(text)
    if not matches:
        return text, 0
    # Replace boundary lines with a single blank so the blank-collapse
    # pass can clean up cleanly later.
    return _BOUNDARY_RE.sub("", text), len(matches)


def _identify_fence_ranges(text: str) -> list[tuple[int, int, int]]:
    """Use markdown-it-py to get fence token ranges. Returns list of
    (open_line_idx, close_line_idx_exclusive, fence_kind) where kind
    is 0 for backtick, 1 for tilde. Open line index is the fence-opener
    line (which carries the info-string); close is the line AFTER the
    closing fence (i.e. the body+closer is `[open+1, close)`)."""
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
    """Reduce a fence info-string to its first whitespace-separated
    token (the language). Returns (new_info, was_stripped).

    Examples:
        'python theme={"slug":"dark"} expandable'  -> ('python', True)
        'bash'                                      -> ('bash',   False)
        ''                                          -> ('',       False)
    """
    info = info.strip()
    if not info:
        return "", False
    parts = info.split(None, 1)
    if len(parts) == 1:
        return parts[0], False
    return parts[0], True


def _token_aware_passes(text: str) -> tuple[str, int, int, int]:
    """Passes 4-7. Walks lines with knowledge of fence ranges:

      - Fence opener lines: rewrite info-string (Mintlify attr strip)
      - Fence body lines: pass through UNCHANGED
      - Prose lines: apply admonition strip + MDX wrapper-tag strip

    Returns (new_text, n_fence_meta, n_admonitions, n_orphan_tags).
    """
    # Find fence ranges via markdown-it-py (one parse).
    fence_ranges = _identify_fence_ranges(text)
    lines = text.split("\n")

    # Build a set of (in_fence_body) line indices for fast skip.
    fence_body_lines: set[int] = set()
    fence_opener_lines: dict[int, int] = {}     # line_idx -> kind
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
            # Inside fence body — DO NOT modify (byte-exact preserve).
            out_lines.append(line)
            continue

        if i in fence_opener_lines:
            # Fence opener — rewrite info-string only.
            kind = fence_opener_lines[i]
            new_line, stripped = _rewrite_fence_opener(line, kind)
            if stripped:
                n_meta += 1
            out_lines.append(new_line)
            continue

        # Prose line — apply admonition + MDX orphan-tag passes.
        new_line, was_admon = _strip_admonition_markers(line)
        if was_admon:
            n_admon += 1
        new_line, n_o = _strip_mdx_wrapper_tags(new_line)
        n_orphan += n_o
        out_lines.append(new_line)

    return "\n".join(out_lines), n_meta, n_admon, n_orphan


def _rewrite_fence_opener(line: str, kind: int) -> tuple[str, bool]:
    """Rewrite a fence opener like ``` python theme={...} expandable
    to ``` python (or ``` if no lang). Returns (new_line, was_changed).
    Indentation + fence-char count + lang preserved; everything after
    the first whitespace-separated token is dropped."""
    fence_char = "~" if kind else "`"
    # Find the fence marker. Allow leading whitespace (block-quoted
    # fences are rare but possible).
    stripped = line.lstrip()
    indent = line[: len(line) - len(stripped)]
    if not stripped.startswith(fence_char * 3):
        return line, False
    # Count fence chars; preserve the count.
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
    # Reassemble.
    new_line = (
        indent + fence_markers + (new_info if new_info else "")
    )
    return new_line.rstrip(), True


def _strip_admonition_markers(line: str) -> tuple[str, bool]:
    """Strip Docusaurus/VitePress `:::tip` open/close markers and
    GitBook `{% hint %}` / `{% endhint %}` / `{% tabs %}` /
    `{% endtabs %}` lines. Inner text preserved (these markers were
    delimiters on their own lines)."""
    for pattern in (
        _ADMON_OPEN_RE, _ADMON_CLOSE_RE,
        _GITBOOK_HINT_OPEN_RE, _GITBOOK_HINT_CLOSE_RE,
        _GITBOOK_TABS_OPEN_RE, _GITBOOK_TABS_CLOSE_RE,
    ):
        if pattern.fullmatch(line):
            return "", True
    return line, False


def _strip_mdx_wrapper_tags(line: str) -> tuple[str, int]:
    r"""Strip MDX/JSX wrapper tags from a prose line, PRESERVING content
    inside inline-code spans (backtick-delimited). Otherwise an
    inline-code span like `<Tip>` (when an author is documenting the
    component itself) would lose its content.

    Algorithm: split the line into alternating prose/inline-code
    segments by walking backticks (with matching backtick counts per
    CommonMark spec); apply the MDX-tag regex ONLY to prose segments;
    reassemble.

    Returns (new_line, n_tags_stripped).
    """
    if "`" not in line:
        # Fast path — no inline code possible, apply regex directly.
        new_line, n_o = _MDX_OPEN_TAG_RE.subn("", line)
        new_line, n_c = _MDX_CLOSE_TAG_RE.subn("", new_line)
        return new_line, n_o + n_c

    parts: list[tuple[str, bool]] = []   # (segment, is_code)
    i = 0
    while i < len(line):
        if line[i] == "`":
            # Count opening backtick run length.
            j = i
            while j < len(line) and line[j] == "`":
                j += 1
            tick_len = j - i
            delim = "`" * tick_len
            # Find matching closer of the same tick length.
            close_at = line.find(delim, j)
            if close_at == -1:
                # Unclosed inline code — treat rest of line as prose so
                # we still strip any orphan tags that follow.
                parts.append((line[i:], False))
                i = len(line)
                continue
            # Capture the code span byte-exactly (including delimiters).
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
        seg, n_o = _MDX_OPEN_TAG_RE.subn("", seg)
        seg, n_c = _MDX_CLOSE_TAG_RE.subn("", seg)
        n_total += n_o + n_c
        rebuilt.append(seg)
    return "".join(rebuilt), n_total


def _whitespace_entity_pass(text: str) -> tuple[str, int, int, int]:
    """Final cosmetic pass: decode whitelisted HTML entities, collapse
    runs of >=3 blank lines to a single blank, strip per-line trailing
    whitespace. Operates on the WHOLE text — entities inside fences
    would be invalid HTML anyway, so safe. (Trailing-ws + blank
    collapse skip fence bodies.)
    """
    # Entity decode (whole-file).
    n_ent = 0
    for src, dst in _ENTITY_DECODES:
        c = text.count(src)
        if c:
            text = text.replace(src, dst)
            n_ent += c

    # Re-identify fences in the post-entity-decoded text so the
    # whitespace passes can skip fence bodies.
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
            # Preserve fence body byte-exact.
            out.append(line)
            blank_streak = 0
            continue
        # Strip trailing whitespace on prose lines.
        if line != line.rstrip():
            n_trail += 1
            line = line.rstrip()
        # Collapse blank-line runs.
        if line == "":
            blank_streak += 1
            if blank_streak >= 3:
                # Drop this line (already kept first 2; this is the 3rd+)
                n_blank += 1
                continue
        else:
            blank_streak = 0
        out.append(line)

    return "\n".join(out), n_ent, n_blank, n_trail
