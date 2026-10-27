"""corpus_normalize — 9-pass markdown cleanup pipeline; pure + idempotent."""
from __future__ import annotations
from . import params, patterns, schemas, versions

import re
import unicodedata
from typing import Optional

from markdown_it import MarkdownIt



def normalize_doc(
    md_text: str,
    *,
    source_url: Optional[str] = None,
) -> schemas.NormalizedDoc:
    """Pure normalization pipeline. Idempotent. No I/O."""
    stats = schemas.NormalizeStats(input_bytes = len(md_text.encode("utf-8")))
    text = md_text

    text, n_zw = _unicode_pass(text)
    stats.zero_width_chars_stripped = n_zw

    text, frontmatter = _frontmatter_pass(text)
    stats.frontmatter_extracted = bool(frontmatter)

    text, n_banner = _agent_banner_pass(text)
    stats.agent_banner_stripped = n_banner

    text, n_footer = _agent_footer_pass(text)
    stats.agent_footer_stripped = n_footer

    text, n_license = _license_header_pass(text)
    stats.license_header_stripped = n_license

    text, promoted = _title_heading_pass(text, frontmatter)
    stats.title_promoted = promoted

    text, n_bound = _boundary_pass(text)
    stats.boundary_markers_stripped = n_bound

    text, n_hoisted = _hoist_nested_fences_pass(text)
    stats.nested_fences_hoisted = n_hoisted

    text, n_meta, n_admon, n_orphan, n_img = _token_aware_passes(text)
    stats.fence_meta_stripped       = n_meta
    stats.container_admonitions     = n_admon
    stats.orphan_tags_stripped      = n_orphan
    stats.img_tags_rewritten        = n_img

    text, n_permalink, n_anchor = _permalink_pass(text)
    stats.permalink_debris_stripped = n_permalink
    stats.heading_anchors_stripped = n_anchor

    text, n_media = _empty_media_pass(text)
    stats.empty_media_stripped = n_media

    text, n_ent, n_blank, n_trail = _whitespace_entity_pass(text)
    stats.html_entities_decoded = n_ent
    stats.blank_lines_collapsed = n_blank
    stats.trailing_ws_lines     = n_trail

    stats.output_bytes = len(text.encode("utf-8"))

    return schemas.NormalizedDoc(
        body = text, frontmatter = frontmatter,
        stats = stats, version = versions.NORMALIZER_VERSION,
    )


def _unicode_pass(text: str) -> tuple[str, int]:
    """NFC + BOM/NBSP/zero-width strip so downstream regexes see canonical codepoints."""
    n = 0
    if text.startswith("﻿"):
        text = text[1:]
    text = text.replace(" ", " ")
    n = len(patterns.ZERO_WIDTH_RE.findall(text))
    if n:
        text = patterns.ZERO_WIDTH_RE.sub("", text)
    text = unicodedata.normalize("NFC", text)
    return text, n


def _frontmatter_pass(text: str) -> tuple[str, dict]:
    """Extract YAML (`---`) or TOML (`+++`) frontmatter → (body, dict); naive key:val / key = val parse to avoid a
    yaml/toml dep."""
    m = patterns.FRONTMATTER_RE.match(text)
    separator = ":"
    if not m:
        m = patterns.TOML_FRONTMATTER_RE.match(text)
        separator = "="
    if not m:
        return text, {}
    raw = m.group("body")
    rest = text[m.end():]
    fm: dict = {}
    for line in raw.splitlines():
        line = line.rstrip()
        if not line or line.startswith("#"):
            continue
        if separator not in line:
            continue
        k, _, v = line.partition(separator)
        key = k.strip()
        val = _unquote(v.strip())
        if key:
            fm[key] = val
    if not fm:
        fm = {"_raw": raw}
    return rest, fm


def _unquote(value: str) -> str:
    """One layer of matching quotes off a frontmatter value (`"'use client'"` is the title `'use client'`)."""
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        return value[1:-1]
    return value


def _title_heading_pass(text: str, frontmatter: dict) -> tuple[str, bool]:
    """Give a page whose title lives only in its frontmatter a heading for it. react.dev's pages (`---\\ntitle: Quick
    Start\\n---`) carry no `# Title`, so once the frontmatter is gone nothing in the page says what it is — and the
    page's sections, which Planner and Synth cut it by, have no heading above them. A page that already has an H1 is left
    as it is; so is one whose frontmatter has no title. Idempotent: the heading it adds is the H1 the next run finds."""
    title = (frontmatter.get("title") or "").strip() if frontmatter else ""
    if not title and frontmatter:
        # react.dev's pages for HTML elements are headed `link: "<link>"`, not `title:` — the key is the tag's own name.
        title = next((v.strip() for k, v in frontmatter.items() if isinstance(v, str) and v.strip() == f"<{k}>"), "")
    if not title or "\n" in title:
        return text, False
    fenced: set[int] = set()
    for (open_idx, close_idx, _) in _identify_fence_ranges(text):
        fenced.update(range(open_idx, close_idx))
    for i, line in enumerate(text.split("\n")):
        if i not in fenced and patterns.H1_LINE_RE.match(line):
            return text, False
    return f"# {title}\n\n{text.lstrip(chr(10))}", True


def _agent_banner_pass(text: str) -> tuple[str, int]:
    """Drop the notice some docs hosts put in front of every page for agents: a block quote before the first heading that
    points at the site's llms.txt (Mintlify's "> ## Documentation Index / > Fetch the complete documentation index at
    …/llms.txt", Qdrant's three lines ending "Use this file for an index of the main pages"). Identical on every page of
    the site and about nothing the page says; only a block quote opening the page is looked at, and only one that links
    an llms.txt or is headed "Documentation Index"."""
    lines = text.splitlines(keepends = True)
    start = 0
    while start < len(lines) and not lines[start].strip():
        start += 1
    end = start
    while end < len(lines) and patterns.QUOTE_LINE_RE.match(lines[end]):
        end += 1
    if end == start or not patterns.AGENT_BANNER_RE.search("".join(lines[start:end])):
        return text, 0
    while end < len(lines) and not lines[end].strip():
        end += 1
    return "".join(lines[end:]), 1


def _agent_footer_pass(text: str) -> tuple[str, int]:
    """Drop the footer a host adds to every page for agents — react.dev's `## Sitemap` with a link to its llms.txt, preceded
    by a `---` rule. Identical on every page of the site (179 of 179 for React) and about nothing the page says. Only the
    last few hundred characters are searched, and only a heading followed by nothing but a link to an llms.txt is taken."""
    start = max(0, len(text) - params.AGENT_FOOTER_TAIL_CHARS)
    m = patterns.AGENT_FOOTER_RE.search(text, start)
    if m is None:
        return text, 0
    kept = text[: m.start()].rstrip()
    cut = max(0, len(kept) - params.AGENT_FOOTER_TAIL_CHARS)
    return kept[:cut] + patterns.TRAILING_RULES_RE.sub("", kept[cut:]) + "\n", 1


def _license_header_pass(text: str) -> tuple[str, int]:
    """Drop a licence header that the docs builder left at the top of a page: the `#`-prefixed lines of a comment whose
    opening it dropped, through the closing `-->` (Hugging Face's `# Licensed under the Apache License…`). Without this
    the first "H1" of the page is the licence's first line. Only a block that mentions a licence is taken."""
    m = patterns.LICENSE_HEADER_RE.match(text)
    if m is None or "licen" not in m.group(0).lower():
        return text, 0
    return text[m.end():], 1


def _boundary_pass(text: str) -> tuple[str, int]:
    matches = patterns.BOUNDARY_RE.findall(text)
    if not matches:
        return text, 0
    return patterns.BOUNDARY_RE.sub("", text), len(matches)


def _identify_fence_ranges(text: str) -> list[tuple[int, int, int]]:
    """Return (open_line_idx, close_exclusive, kind) tuples; kind=0 backtick, 1 tilde.

    HTML blocks are switched off: a bare `<pre />` (a bundle that lost the tag's content) opens an HTML block that, per
    CommonMark, only a closing `</pre>` ends — SGLang's bundle has one at line 998, and every fence in the remaining
    146,706 lines then read as HTML, so none of them was recognised, protected or cleaned. Fences are all this needs."""
    md = MarkdownIt("commonmark").disable("html_block")
    ranges: list[tuple[int, int, int]] = []
    for tok in md.parse(text):
        if tok.type != "fence" or tok.map is None:
            continue
        start, end = tok.map
        kind = 1 if (tok.markup and tok.markup[0] == "~") else 0
        ranges.append((start, end, kind))
    ranges.sort()
    return ranges


def _hoist_nested_fences_pass(text: str) -> tuple[str, int]:
    """Dedent fenced blocks CommonMark can't see so the vault can.

    Sphinx autodoc pages put every code example inside a `<dd>`, which
    markdownify renders as a `:   ` definition-list body with all lines —
    fences included — indented 4 spaces. CommonMark has no definition lists,
    so those fences parse as indented code/paragraph text, never as `fence`
    tokens: the vault saw nothing, the writer got no code, and whole chapters
    shipped prose-only (confirmed 2026-10-03: asyncio 0/18 code refs,
    elasticsearch-python 0/102). Only blocks the parser did NOT already
    recognise are touched — fences inside list items etc. are left in place.
    The block is dedented by its opener's indent and set off by blank lines;
    the dd prose around it keeps its indentation. Idempotent."""
    recognised: set[int] = set()
    for (open_idx, close_idx, _) in _identify_fence_ranges(text):
        recognised.update(range(open_idx, close_idx))

    lines = text.split("\n")
    out: list[str] = []
    n = 0
    i = 0
    while i < len(lines):
        m = None if i in recognised else patterns.INDENTED_FENCE_OPEN_RE.match(lines[i])
        if m is None or (m.group("fence")[0] == "`" and "`" in m.group("info")):
            out.append(lines[i])
            i += 1
            continue
        indent, fence = m.group("indent"), m.group("fence")
        close = None
        for j in range(i + 1, len(lines)):
            body = lines[j]
            if not body.strip():
                continue
            if not body.startswith(indent):
                break    # not uniformly nested — leave untouched
            inner = body[len(indent):].rstrip()
            if inner and set(inner) == {fence[0]} and len(inner) >= len(fence):
                close = j
                break
        if close is None:
            out.append(lines[i])
            i += 1
            continue
        if out and out[-1].strip():
            out.append("")
        out.extend(
            ln[len(indent):] if ln.startswith(indent) else ln.strip()
            for ln in lines[i:close + 1]
        )
        if close + 1 < len(lines) and lines[close + 1].strip():
            out.append("")
        n += 1
        i = close + 1
    return "\n".join(out), n


def _strip_fence_info_string(info: str) -> tuple[str, bool]:
    """Reduce fence info-string to first token (lang), stripping Mintlify attrs."""
    info = info.strip()
    if not info:
        return "", False
    parts = info.split(None, 1)
    if len(parts) == 1:
        return parts[0], False
    return parts[0], True


def _token_aware_passes(text: str) -> tuple[str, int, int, int, int]:
    """Fence-range-aware line walk: rewrite openers, skip bodies, strip prose tags (and rewrite JSX `<img>` as Markdown)."""
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
    n_img    = 0

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
        new_line, n_i = _rewrite_img_tags(new_line)
        n_img += n_i
        out_lines.append(new_line)

    return "\n".join(out_lines), n_meta, n_admon, n_orphan, n_img


def _rewrite_fence_opener(line: str, kind: int) -> tuple[str, bool]:
    """Strip Mintlify attrs from fence opener line."""
    fence_char = "~" if kind else "`"
    # The opener may sit inside a callout (`> ```bash theme={…}`): leave the indentation and quote markers as they are.
    indent = patterns.QUOTE_PREFIX_RE.match(line).group(0)
    stripped = line[len(indent):]
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
        patterns.ADMON_OPEN_RE, patterns.ADMON_CLOSE_RE,
        patterns.GITBOOK_HINT_OPEN_RE, patterns.GITBOOK_HINT_CLOSE_RE,
        patterns.GITBOOK_TABS_OPEN_RE, patterns.GITBOOK_TABS_CLOSE_RE,
    ):
        if pattern.fullmatch(line):
            return "", True
    return line, False


def _permalink_pass(text: str) -> tuple[str, int, int]:
    """Strip static-site permalink debris from prose lines, fence-aware
    (code samples quoting `[¶](#…)` markup must survive verbatim). The anchor Hugging Face's doc builder appends to a
    heading (`## AutoModel[[transformers.AutoModel]]`) goes the same way; it is counted apart."""
    fence_ranges = _identify_fence_ranges(text)
    fenced: set[int] = set()
    for (open_idx, close_idx, _) in fence_ranges:
        # Same convention as _token_aware_passes: opener + body lines are
        # fence territory; close_idx itself is exclusive (first line after).
        fenced.add(open_idx)
        fenced.update(range(open_idx + 1, close_idx))
    n = 0
    n_anchor = 0
    out: list[str] = []
    for i, line in enumerate(text.split("\n")):
        if i in fenced:
            out.append(line)
            continue
        new_line = patterns.PERMALINK_LEADING_RE.sub("", line)
        new_line = patterns.PERMALINK_TRAILING_RE.sub("", new_line)
        if new_line != line:
            n += 1
        if "[[" in new_line:
            anchored = patterns.HEADING_ANCHOR_RE.sub(r"\1", new_line)
            if anchored != new_line:
                n_anchor += 1
                new_line = anchored
        out.append(new_line)
    return "\n".join(out), n, n_anchor


def _empty_media_pass(text: str) -> tuple[str, int]:
    """Drop `<video>` / `<audio>` elements that point at nothing: some bundles keep the tag and lose its `src`
    (Browser Use: `<video />`, or a `<video>` of `<source type="video/mp4" />`), leaving a stray tag in the middle of the
    prose. One with a `src` anywhere inside it is kept, as is anything inside a code fence, and a tag that shares its
    line with other text. Idempotent."""
    fenced: set[int] = set()
    for (open_idx, close_idx, _) in _identify_fence_ranges(text):
        fenced.update(range(open_idx, close_idx))
    lines = text.split("\n")
    out: list[str] = []
    n = 0
    i = 0
    while i < len(lines):
        m = None if i in fenced else patterns.MEDIA_OPEN_RE.match(lines[i])
        if m is None:
            out.append(lines[i])
            i += 1
            continue
        closing = f"</{m.group('tag').lower()}>"
        rest = m.group("rest").strip().lower()
        if m.group("selfclose") or rest == closing:
            end = i
        elif rest == "":
            end = next(
                (j for j in range(i + 1, min(i + 1 + params.MEDIA_MAX_LINES, len(lines))) if closing in lines[j].lower()),
                None,
            )
        else:
            end = None
        if end is None:
            out.append(lines[i])
            i += 1
            continue
        if patterns.MEDIA_SRC_RE.search("\n".join(lines[i : end + 1])):
            out.extend(lines[i : end + 1])
        else:
            n += 1
        i = end + 1
    return "\n".join(out), n


def _strip_mdx_wrapper_tags(line: str) -> tuple[str, int]:
    """Strip MDX/JSX wrapper tags and MDX comments from a prose line; preserves inline-code spans."""
    if "`" not in line:
        # Fast path — no inline code possible, apply regex directly.
        new_line, n_o = patterns.MDX_OPEN_TAG_RE.subn("", line)
        new_line, n_c = patterns.MDX_CLOSE_TAG_RE.subn("", new_line)
        new_line, n_m = patterns.MDX_COMMENT_RE.subn("", new_line)
        return new_line, n_o + n_c + n_m

    n_total = 0
    rebuilt: list[str] = []
    for seg, is_code in _inline_code_segments(line):
        if is_code:
            rebuilt.append(seg)
            continue
        seg, n_o = patterns.MDX_OPEN_TAG_RE.subn("", seg)
        seg, n_c = patterns.MDX_CLOSE_TAG_RE.subn("", seg)
        seg, n_m = patterns.MDX_COMMENT_RE.subn("", seg)
        n_total += n_o + n_c + n_m
        rebuilt.append(seg)
    return "".join(rebuilt), n_total


def _inline_code_segments(line: str) -> list[tuple[str, bool]]:
    """A prose line cut into `(segment, is_inline_code)` pieces, so a pass can leave what is quoted in backticks alone."""
    parts: list[tuple[str, bool]] = []
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
    return parts


def _rewrite_img_tags(line: str) -> tuple[str, int]:
    """`<img className="…" src="x.png" alt="y" />` → `![y](x.png)` on a prose line (react.dev writes its figures that way);
    the `dark-image` copy of a light/dark pair is dropped. A tag with no `src` is left as it is, as is anything quoted in
    backticks."""
    if "<img" not in line.lower():
        return line, 0
    n = 0

    def rewrite(m: re.Match) -> str:
        nonlocal n
        attrs = m.group("attrs")
        src = patterns.IMG_SRC_RE.search(attrs)
        if src is None:
            return m.group(0)
        n += 1
        if patterns.IMG_DARK_RE.search(attrs):
            return ""
        alt = patterns.IMG_ALT_RE.search(attrs)
        return f"![{(alt.group(1) or alt.group(2) or '') if alt else ''}]({src.group(1) or src.group(2)})"

    rebuilt = [seg if is_code else patterns.IMG_TAG_RE.sub(rewrite, seg) for seg, is_code in _inline_code_segments(line)]
    return "".join(rebuilt), n


def _whitespace_entity_pass(text: str) -> tuple[str, int, int, int]:
    """Final cosmetic pass: decode whitelisted HTML entities, collapse
    runs of ≥3 blank lines to a single blank, strip per-line trailing
    whitespace."""
    n_ent = 0
    for src, dst in params.ENTITY_DECODES:
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
