"""Monolith splitter: Source-marker → H1 → H2/H3 precedence (Docusaurus: single H1 wraps the whole bundle). markdown-it tokens keep code fences/tables/HTML atomic."""
from __future__ import annotations
import domains
from . import params, patterns

import hashlib
import logging
import re
from bisect import bisect_right
from collections import Counter
from urllib.parse import urljoin, urlparse

from markdown_it import MarkdownIt
from mdit_py_plugins.deflist import deflist_plugin
from mdit_py_plugins.footnote import footnote_plugin
from mdit_py_plugins.front_matter import front_matter_plugin



logger = logging.getLogger(__name__)


def split_markdown_by_headings(
    text: str,
    split_levels: tuple[int, ...] = (1,),
) -> list[tuple[str, str, str, str]]:
    """[(h1, h2, body, source_path), ...] in doc order. Driven by markdown-it
    tokens so code fences/tables/HTML stay atomic. `source_path` is always ""
    here — a pure heading split has no per-section URL to report (see
    split_by_source_markers for the strategy that does)."""
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
        return [("", "", text, "")] if text else []
    sections: list[tuple[str, str, str, str]] = []
    if boundaries[0][0] > 0:
        preamble = "".join(lines[:boundaries[0][0]])
        if preamble.strip():
            sections.append(("", "", preamble, ""))
    current_h1 = ""
    for j, (start, level, heading_text) in enumerate(boundaries):
        end = boundaries[j + 1][0] if j + 1 < len(boundaries) else len(lines)
        body = "".join(lines[start:end])
        if level == 1:
            current_h1 = heading_text
            sections.append((heading_text, "", body, ""))
        else:
            sections.append((current_h1, heading_text, body, ""))
    return sections


def slugify_heading(s: str, fallback: str) -> str:
    s2 = re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")[:60]
    return s2 or fallback


def _fallback_heading(body: str) -> str:
    """First substantive prose line for heading-less split sections (bare
    `#` anchor headings). Skips fences, blank lines, blockquotes, list
    markers, and markdown links/images — returns "" when nothing qualifies,
    letting the caller fall through to its positional fallback."""
    in_fence = False
    for line in (body or "").splitlines():
        s = line.strip()
        if s.startswith("```"):
            in_fence = not in_fence
            continue
        if in_fence or not s:
            continue
        if s[0] in (">", "-", "*", "+", "|"):
            continue
        if re.match(r"^\d+[.)]\s+", s):
            continue
        s = re.sub(r"!?\[[^\]]*\]\([^)]*\)", "", s).strip()
        s = re.sub(r"[*_`~#]+", "", s).strip()
        if len(s) >= 20 and re.search(r"[a-zA-Z]{3,}", s):
            return s[:120]
    return ""


def _resolve_display_heading(lines: list[str], h1_idx: int, fallback_title: str) -> str:
    """Prefer a SECOND H1 appearing shortly after `h1_idx` (past the `Source:`
    line and any blank lines) over the one at h1_idx itself. Some llms-full.txt
    generators wrap the real title in a throwaway filename-derived heading —
    FastMCP's Python-SDK reference pages read:

        # __init__
        Source: https://gofastmcp.com/python-sdk/fastmcp-apps-__init__

        # `fastmcp.apps`
        FastMCP Apps — interactive UIs for MCP tools.

    `# __init__`/`# base` repeats across every module (11×/8× for FastMCP
    alone) and is a worse slug/title than the real heading three lines down.
    Stops looking the moment real prose appears with no second heading —
    the ordinary single-H1 case is untouched."""
    for cursor in range(h1_idx + 1, min(h1_idx + 1 + params.HEADING_LOOKAHEAD_LINES, len(lines))):
        stripped = lines[cursor].strip()
        if not stripped or stripped.startswith("Source:"):
            continue
        if stripped.startswith("# ") and not stripped.startswith("## "):
            return stripped[2:].strip().strip("`")
        break
    return fallback_title


def will_be_split(body: str) -> bool:
    """Whether `split_monolith` would take this single page apart (it leaves anything under the threshold alone)."""
    return len(body.encode("utf-8")) >= params.MONOLITH_SPLIT_THRESHOLD_BYTES


_CHILD_HEADING_RE = re.compile(r"^#{2,4}\s+(.+?)\s*#*\s*$")
_UPDATE_LABEL_RE = re.compile(r"""<Update\s+label=["']([^"']+)["']""")


def _plain_heading(text: str) -> str:
    """A heading as a title: no markdown link syntax (`[v2.9.1: Call Me Maybe](https://…)` reads as its text), backticks,
    emphasis marks or trailing `#`s, one line, bounded."""
    s = patterns.MD_LINK_TEXT_RE.sub(r"\1", text)
    s = re.sub(r"[`*]+", "", s).strip().rstrip("#").strip()
    # A converter escapes what Markdown would read as markup: `all\_inner\_texts` is the title "all_inner_texts".
    s = re.sub(r"\\([\\_*\[\]()#+.!|<>-])", r"\1", s)
    return re.sub(r"\s+", " ", s)[: params.TITLE_MAX_CHARS]


def strip_site_suffix(titles: list[str]) -> list[str]:
    """The titles without the site's name when a page's `<title>` carried it: "Quick start | Grafana Tempo documentation" →
    "Quick start", "Context overview - Docs by LangChain" → "Context overview". Every page of a site ends the same way, which
    is noise in a list of pages and in what Planner is told. Only a trailing segment (after ` | `, ` - `, ` – `, ` — `, ` · `)
    that is the same on at least TITLE_SUFFIX_MIN_SHARE of the titles — and at least TITLE_SUFFIX_MIN_COUNT of them — is
    taken, so "List - Users" is safe in a docs set that names one resource now and then. Titles made up of a parent and a
    child (`Parent › Child`) are left alone and are not counted. Another suffix with the same last word ("Grafana
    Enterprise Traces documentation" beside "Grafana Tempo documentation") goes too, when it is seen as often as the minimum."""
    def split(title: str) -> tuple[str, str] | None:
        best, cut = -1, ""
        for sep in params.TITLE_SUFFIX_SEPARATORS:
            at = title.rfind(sep)
            if at > best:
                best, cut = at, sep
        if best <= 0:
            return None
        head, tail = title[:best].strip(), title[best + len(cut):].strip()
        return (head, tail) if head and tail else None

    parts = [None if " › " in t else split(t) for t in titles]
    eligible = sum(1 for t in titles if " › " not in t)
    if eligible < params.TITLE_SUFFIX_MIN_COUNT:
        return list(titles)
    counts = Counter(p[1].casefold() for p in parts if p is not None)
    dominant = {s for s, c in counts.items() if c >= params.TITLE_SUFFIX_MIN_COUNT and c / eligible >= params.TITLE_SUFFIX_MIN_SHARE}
    out = list(titles)
    if dominant:
        last_words = {s.rsplit(" ", 1)[-1] for s in dominant if " " in s}
        related = {s for s, c in counts.items() if c >= params.TITLE_SUFFIX_MIN_COUNT and " " in s and s.rsplit(" ", 1)[-1] in last_words}
        drop = dominant | related
        out = [p[0] if p is not None and p[1].casefold() in drop else t for t, p in zip(titles, parts)]
    return _strip_site_prefix(out, eligible)


def _strip_site_prefix(titles: list[str], eligible: int) -> list[str]:
    """The same for a site's name at the FRONT of a title ("TypeScript: Documentation - Everyday Types": 26 of TypeScript's 42
    pages), when what follows differs from page to page. Refused when it would make titles collide that were distinct."""
    def split_front(title: str) -> tuple[str, str] | None:
        at, cut = min(((title.find(sep), sep) for sep in params.TITLE_SUFFIX_SEPARATORS if title.find(sep) > 0), default = (-1, ""))
        if at <= 0:
            return None
        head, rest = title[:at].strip(), title[at + len(cut):].strip()
        return (head, rest) if head and rest else None

    fronts = [None if " › " in t else split_front(t) for t in titles]
    counts = Counter(f[0].casefold() for f in fronts if f is not None)
    drop = {h for h, c in counts.items() if c >= params.TITLE_SUFFIX_MIN_COUNT and c / max(1, eligible) >= params.TITLE_SUFFIX_MIN_SHARE}
    if not drop:
        return titles
    stripped = [f[1] if f is not None and f[0].casefold() in drop else t for t, f in zip(titles, fronts)]
    if len({t.casefold() for t in stripped}) < len({t.casefold() for t in titles}):
        return titles
    return stripped


def display_title(body: str, fallback: str) -> str:
    """The human title of a stored page, so the explorer can list pages by name instead of by slug.

    The page's first H1 (past a throwaway filename-derived one — `_resolve_display_heading`). A page split out of
    a bigger one starts `# Parent` and then, with nothing but blank lines between, its own heading — `### FileChanged`
    or a changelog `<Update label="v3.2.3">` — so those read "Parent › Child" and stay distinguishable from their
    siblings. A page whose H1 is followed by a `Source:` line is a whole page, never a child. With no H1 at all, the
    first sentence of prose, then `fallback`."""
    lines = (body or "").splitlines()
    h1_idx = next(
        (i for i, ln in enumerate(lines[: params.TITLE_SCAN_LINES]) if ln.startswith("# ") and not ln.startswith("## ")),
        None,
    )
    if h1_idx is None:
        return _plain_heading(_fallback_heading(body)) or fallback
    title = _plain_heading(_resolve_display_heading(lines, h1_idx, lines[h1_idx][2:].strip())) or fallback
    after = [ln.strip() for ln in lines[h1_idx + 1 : h1_idx + 1 + params.TITLE_CHILD_LOOKAHEAD_LINES] if ln.strip()]
    first = after[0] if after else ""
    if first.startswith("Source:"):
        return title
    child = _CHILD_HEADING_RE.match(first) or _UPDATE_LABEL_RE.search(first)
    if child:
        sub = _plain_heading(child.group(1))
        if sub and sub.lower() != title.lower():
            # An H1 of one or two characters is an index letter some bundles file their sections under (Next.js:
            # `# C` over "Cache Components"), not a parent worth naming.
            return sub if len(title) <= 2 else f"{title} › {sub}"[: params.TITLE_MAX_CHARS]
    return title


def _section_label(segment: str) -> str:
    """A URL folder as a label: `legacy-actor` → `Legacy Actor`."""
    return " ".join(w[:1].upper() + w[1:] for w in re.sub(r"[-_]+", " ", segment).split())


def _leaf_label(url: str, title: str) -> str:
    """The page's own file name as a label, minus the title it ends with: `dashboard_overview` for a page titled
    "Overview" is "Dashboard". "" when it adds nothing."""
    segments = [s for s in urlparse(url).path.split("/") if s]
    if not segments:
        return ""
    label = _section_label(re.sub(r"\.\w{1,5}$", "", segments[-1]))
    if label.casefold() != title.casefold() and label.casefold().endswith(title.casefold()):
        label = label[: -len(title)].strip()
    return "" if label.casefold() == title.casefold() else label


def disambiguate_titles(pages: list[tuple[str, str]]) -> list[str]:
    """[(title, page URL), ...] → the titles, in the same order, with any that repeat told apart by where their page
    lives: three pages called "All Parameters" become "Agent › All Parameters", "Browser › All Parameters" and so on,
    using the fewest parent folders that make them distinct — or, when they share a folder, their own file names
    ("Dashboard › Overview"). Titles that are already unique, and repeats whose URLs give nothing to tell them apart by,
    come back as they were."""
    titles = [title for title, _url in pages]
    groups: dict[str, list[int]] = {}
    for i, (title, _url) in enumerate(pages):
        if title.strip():
            groups.setdefault(title.strip().casefold(), []).append(i)
    for members in groups.values():
        if len(members) < 2:
            continue
        folders = {
            i: [
                s for s in [p for p in urlparse(pages[i][1]).path.split("/") if p][:-1]
                if s.lower() not in params.TITLE_SKIP_SEGMENTS and not patterns.VERSION_SEGMENT_RE.match(s)
            ]
            for i in members
        }
        candidates = [
            {i: " › ".join(_section_label(s) for s in folders[i][-depth:]) for i in members}
            for depth in range(1, params.TITLE_CONTEXT_MAX_SEGMENTS + 1)
        ]
        candidates.append({i: _leaf_label(pages[i][1], pages[i][0]) for i in members})
        # Neither alone told them apart (Diffusers' four "ControlNet"s: two share a folder, and the other two's file names
        # are the title): the nearest folder with the file name, when it adds one.
        candidates.append({
            i: " › ".join(filter(None, [
                _section_label(folders[i][-1]) if folders[i] else "",
                _leaf_label(pages[i][1], pages[i][0]),
            ]))
            for i in members
        })
        labels = next((c for c in candidates if len(set(c.values())) == len(members)), None)
        if labels is None:
            continue
        for i in members:
            if labels[i]:
                titles[i] = f"{labels[i]} › {pages[i][0]}"[: params.TITLE_MAX_CHARS]
    return titles


def _nav_key(url: str) -> str:
    """A page URL the way an index and a bundle may each spell it: no query or fragment, no `.md`, no trailing slash."""
    bare = url.split("#", 1)[0].split("?", 1)[0].rstrip("/")
    return bare[:-3] if bare.endswith(".md") else bare


def order_by_nav(
    writes: list[tuple[str, str, str]],
    nav_urls: list[str],
) -> list[tuple[str, str, str]]:
    """Put split pages `[(slug, body, source_path), ...]` in the order the site's own index lists them (`nav_urls`, from
    its `llms.txt`), which is its navigation order — a bundle is often alphabetical by URL, which files the introduction
    among the C's. Stable: pages that share a URL (a sub-split) stay together in the order they had, and pages the index
    does not list keep their order after the listed ones. An index that names fewer than NAV_MIN_MATCH_RATIO of the
    pages is not this site's navigation, and the order is left alone."""
    if not nav_urls or not writes:
        return writes
    position: dict[str, int] = {}
    for rank, url in enumerate(nav_urls):
        position.setdefault(_nav_key(url), rank)
    ranks = [position.get(_nav_key(source)) if source else None for _slug, _body, source in writes]
    if sum(rank is not None for rank in ranks) < params.NAV_MIN_MATCH_RATIO * len(writes):
        return writes
    unlisted = len(nav_urls)
    order = sorted(range(len(writes)), key = lambda i: (unlisted if ranks[i] is None else ranks[i], i))
    return [writes[i] for i in order]


def sections_by_nav(
    writes: list[tuple[str, str, str]],
    nav_index: list[tuple[str, str]],
) -> list[str]:
    """The site's own section heading for each split page `[(slug, body, source_path), ...]`, from its index
    `[(url, section), ...]` (the `##` headings of its llms.txt): Claude Code's "Getting started", "Plugins", … — a
    better grouping than the URL folders, which for it are all `docs/en`. `""` for a page the index does not list.
    All `""` unless the index names at least NAV_MIN_SECTIONS different sections across these pages and lists
    NAV_MIN_MATCH_RATIO of them: one heading over everything ("Docs") groups nothing, and a foreign index is no guide."""
    blank = [""] * len(writes)
    if not nav_index or not writes:
        return blank
    section_of = {_nav_key(url): _plain_heading(section)[:60] for url, section in reversed(nav_index)}
    found = [section_of.get(_nav_key(source)) if source else None for _slug, _body, source in writes]
    if sum(s is not None for s in found) < params.NAV_MIN_MATCH_RATIO * len(writes):
        return blank
    sections = [s or "" for s in found]
    return sections if len({s for s in sections if s}) >= params.NAV_MIN_SECTIONS else blank


def absolutize_links(body: str, base_url: str, *, relative: bool = True) -> str:
    """Make a page's site-relative links absolute, against the page's own URL: `/open-source/x` and, unless `relative`
    is off, `./y` and `../z` too. In this app a bare `/open-source/x` would open a page of Nexus that does not exist.
    Anchors, absolute URLs, `mailto:` and the like are left alone, as is everything inside code (fenced or inline) — and the
    stored copy of a page's image (`/api/v1/dd/ingestion/<slug>/artifacts/…`), which is this app's own path: made absolute it
    named a page on the docs' host (`https://huggingface.co/api/v1/…`) and every such image was broken."""
    if not base_url or "](" not in body:
        return body
    host = (urlparse(base_url).netloc or "").lower()
    if not host or host in params.LINK_REWRITE_SKIP_HOSTS:
        return body
    own = domains.dd.ingestion.artifacts.keys.OWN_ARTIFACT_URL_MARKERS

    def resolve(match: re.Match) -> str:
        dest = match.group(2)
        if dest.startswith("//") or dest.startswith(own):
            return match.group(0)
        if dest.startswith("/") or (relative and dest.startswith(("./", "../"))):
            return f"{match.group(1)}{urljoin(base_url, dest)}{match.group(3)}"
        return match.group(0)

    out: list[str] = []
    fence: tuple[str, int] | None = None  # (character, length) of the fence we are inside, if any
    for line in body.split("\n"):
        marker = patterns.FENCE_LINE_RE.match(line.lstrip(" \t>"))
        if marker:
            run, after = marker.group(1), marker.group(2)
            if fence is None:
                if run[0] == "~" or "`" not in after:  # a backtick run with more backticks after it is inline code, not a fence
                    fence = (run[0], len(run))
            elif run[0] == fence[0] and len(run) >= fence[1] and not after.strip():
                fence = None
            out.append(line)
        elif fence is not None or "](" not in line:
            out.append(line)
        else:
            pieces: list[str] = []
            cursor = 0
            for span in patterns.INLINE_CODE_RE.finditer(line):
                pieces.append(patterns.MD_LINK_TARGET_RE.sub(resolve, line[cursor : span.start()]))
                pieces.append(span.group(0))
                cursor = span.end()
            pieces.append(patterns.MD_LINK_TARGET_RE.sub(resolve, line[cursor:]))
            out.append("".join(pieces))
    return "\n".join(out)


def worth_keeping(body: str, *, delimited: bool, floor: int | None = None) -> bool:
    """Whether a page is a page rather than a fragment. Under the floor (SPLIT_MIN_SECTION_BYTES unless a tier gives its
    own) it is a fragment — unless the source itself marked it off as a page (`delimited`: a `Source:` line in a
    bundle, or a page fetched on its own) and it carries a code example, which is real documentation however short
    (Browser Use's "Remove Tools" is a sentence and the call that does it; FastHTML's websocket example is a script).
    Link lists and one-line package descriptions have no code and are still dropped."""
    if len(body.encode("utf-8")) >= (params.SPLIT_MIN_SECTION_BYTES if floor is None else floor):
        return True
    return delimited and patterns.CODE_FENCE_RE.search(body) is not None


def split_by_source_markers(text: str) -> list[tuple[str, str, str, str]] | None:
    """Boundary strategy A: walk back from each `Source: <url>` to the nearest
    H1 — that's the section start. None if < SOURCE_MIN_MARKERS markers.
    The matched URL is carried through as source_path — it's the only thing
    that distinguishes sibling split pages once they all inherit the same
    parent bundle URL (see storage.entities.ManifestEntry.source_path)."""
    matches = list(patterns.SOURCE_LINE_RE.finditer(text))
    if len(matches) < params.SOURCE_MIN_MARKERS:
        return None
    lines = text.splitlines(keepends = True)
    line_offsets = [0]
    for line in lines:
        line_offsets.append(line_offsets[-1] + len(line))
    def _char_to_line_idx(pos: int) -> int:
        # Binary search: this runs once per `Source:` marker over every line of a bundle of hundreds of thousands.
        return min(bisect_right(line_offsets, pos) - 1, len(lines) - 1)
    section_starts: list[tuple[int, str, str]] = []
    seen_starts: set[int] = set()
    for m in matches:
        src_line_idx = _char_to_line_idx(m.start())
        source_url = m.group(1)
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
        h1_title = _resolve_display_heading(lines, h1_idx, h1_title)
        if h1_idx in seen_starts:
            continue
        seen_starts.add(h1_idx)
        section_starts.append((h1_idx, h1_title, source_url))
    section_starts.sort(key = lambda s: s[0])
    sections: list[tuple[str, str, str, str]] = []
    if section_starts and section_starts[0][0] > 0:
        preamble = "".join(lines[:section_starts[0][0]])
        if preamble.strip():
            sections.append(("", "", preamble, ""))
    for j, (h1_idx, h1_title, source_url) in enumerate(section_starts):
        end = (section_starts[j + 1][0]
               if j + 1 < len(section_starts) else len(lines))
        body = "".join(lines[h1_idx:end])
        sections.append((h1_title, "", body, source_url))
    return sections


def split_monolith(
    body: str,
    parent_slug: str,
) -> tuple[list[tuple[str, str, str]], int, int]:
    """→ (writes, stubs_dropped, dupes_dropped) where writes is
    [(slug, body, source_path), ...]. Pure — caller persists. Below
    MONOLITH_SPLIT_THRESHOLD_BYTES returns body unchanged."""
    if len(body.encode("utf-8")) < params.MONOLITH_SPLIT_THRESHOLD_BYTES:
        return [(parent_slug, body, "")], 0, 0
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
        expanded = _h2_subsplit_oversized([(parent_slug, body, "")], parent_slug)
        if len(expanded) <= 1:
            return [(parent_slug, body, "")], 0, 0
        # Cross-level dedup — recursion can split footers into duplicates.
        seen: set[str] = set()
        deduped: list[tuple[str, str, str]] = []
        dropped = 0
        for s, b, sp in expanded:
            h = hashlib.sha256(b.encode("utf-8")).hexdigest()
            if h in seen:
                dropped += 1
                continue
            seen.add(h)
            deduped.append((s, b, sp))
        if dropped:
            logger.info(
                f"[post] fall-through dedup: -{dropped} dupes; "
                f"{len(deduped)} survive (was {len(expanded)})"
            )
        return _disambiguate_duplicate_slugs(deduped), 0, dropped
    logger.info(
        f"[post] split_monolith: {strategy} → {len(sections)} sections "
        f"(input {len(body)//1024} KB)"
    )
    width = max(4, len(str(max(0, len(sections) - 1))))
    writes: list[tuple[str, str, str]] = []
    for i, (h1, h2, sec_body, source_url) in enumerate(sections):
        # H1 (parent) wins over H2 to preserve parent context. Empty H1+H2
        # (anchor-only headings like print.html's bare `#` lines — all 109
        # Rust sections hit this) falls back to the first substantive prose
        # line before surrendering to `section-NNNN`, so slugs/titles carry
        # signal for the explorer + Planner seeds instead of position.
        heading = h1 or h2 or _fallback_heading(sec_body) or f"section-{i:0{width}d}"
        sub = slugify_heading(heading, f"section-{i:0{width}d}")
        base = sub if sub.startswith(parent_slug) else f"{parent_slug}-{sub}"
        # Each section knows its own URL, which a bundle's `./x` and `../x` links are relative to.
        writes.append((base, absolutize_links(sec_body, source_url) if source_url else sec_body, source_url))
    pre = len(writes)
    delimited = strategy == "source-markers"
    writes = [(s, b, sp) for s, b, sp in writes if worth_keeping(b, delimited = delimited)]
    stubs_dropped = pre - len(writes)
    seen: set[str] = set()
    deduped: list[tuple[str, str, str]] = []
    duplicates_dropped = 0
    for s, b, sp in writes:
        h = hashlib.sha256(b.encode("utf-8")).hexdigest()
        if h in seen:
            duplicates_dropped += 1
            continue
        seen.add(h)
        deduped.append((s, b, sp))
    writes = deduped
    if stubs_dropped or duplicates_dropped:
        logger.info(
            f"[post] split cleanup: -{stubs_dropped} stubs, "
            f"-{duplicates_dropped} dupes; {len(writes)} survive (was {pre})"
        )
    # Oversized sections with <2 viable sub-pages stay intact (Dask numpy compat).
    writes = _h2_subsplit_oversized(writes, parent_slug)
    return _disambiguate_duplicate_slugs(writes), stubs_dropped, duplicates_dropped


def _disambiguate_duplicate_slugs(
    writes: list[tuple[str, str, str]],
) -> list[tuple[str, str, str]]:
    """Append a short content-hash suffix to slugs that collide AND have no
    `source_path` to disambiguate them with in the UI. A real source_path
    (Mintlify-style `Source:` markers) already makes duplicate headings
    distinguishable by page — this only fires for bundles that never had
    per-section URLs to begin with (dbt/Pydantic: 0 `Source:` lines anywhere;
    dbt's `### Fetching data...` heading alone repeats across 26 unrelated
    API-reference pages with nothing else to tell them apart)."""
    counts = Counter(s for s, _b, sp in writes if not sp)
    out: list[tuple[str, str, str]] = []
    for s, b, sp in writes:
        if not sp and counts[s] > 1:
            suffix = hashlib.sha256(b.encode("utf-8")).hexdigest()[:6]
            out.append((f"{s}-{suffix}", b, sp))
        else:
            out.append((s, b, sp))
    return out


def _looks_like_changelog(h1_text: str, body: str) -> bool:
    # Substring, not exact match — real headings vary ("Claude Code
    # changelog", "FastMCP Changelog"); the `<Update label=...>` count is
    # the real signal, the title just gates against false positives on an
    # unrelated oversized section that happens to contain a couple of them.
    return (
        "changelog" in h1_text.strip().lower()
        and len(patterns.UPDATE_TAG_RE.findall(body)) >= 2
    )


def _split_changelog_releases(
    slug: str,
    body: str,
    source_path: str,
    h1_prefix: str,
) -> list[tuple[str, str, str]] | None:
    """Split a `# Changelog` section on `<Update label="vX.Y.Z">` boundaries
    instead of its internal (repeating, non-unique) H2s. Gives each release
    its own real identity — `slug` carries CHANGELOG_RELEASE_MARKER so
    post.service can tag these `tier = "changelog"` for corpus_load to drop
    from the Planner/Synth path without spending an off_topic LLM call on
    ~dozens of near-duplicate "New Contributors" fragments per release."""
    matches = list(patterns.UPDATE_TAG_RE.finditer(body))
    if len(matches) < 2:
        return None
    out: list[tuple[str, str, str]] = []
    seen_slugs: set[str] = set()
    for i, m in enumerate(matches):
        start = m.start()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(body)
        release_body = body[start:end]
        if h1_prefix and not release_body.startswith(h1_prefix):
            release_body = h1_prefix + release_body
        version_slug = slugify_heading(m.group(1), f"release-{i}")
        base = slug[:-len("-changelog")] if slug.endswith("-changelog") else slug
        new_slug = f"{base}{patterns.CHANGELOG_RELEASE_MARKER}{version_slug}"
        if new_slug in seen_slugs:
            new_slug = f"{new_slug}-{i}"
        seen_slugs.add(new_slug)
        out.append((new_slug, release_body, source_path))
    return out


def _is_fragmented_changelog(h1_text: str, sub_pages: list[tuple[str, str, str]]) -> bool:
    """Whether a changelog's sub-pages are fragments rather than releases. A changelog without `<Update>` tags is cut on
    its internal H2s, which in FastMCP's are `## New Contributors` / `## What's Changed` under every release: 126 pages
    with three titles between them, none of which says which release it belongs to. One per version (Dask's
    `## 2024.5.0`) is what the split is for and is kept."""
    if "changelog" not in h1_text.lower() or len(sub_pages) < 2:
        return False
    titles = {display_title(body, slug).casefold() for slug, body, _source in sub_pages}
    return len(titles) < params.CHANGELOG_MIN_DISTINCT_RATIO * len(sub_pages)


def _h2_subsplit_oversized(
    writes: list[tuple[str, str, str]],
    parent_slug: str,
) -> list[tuple[str, str, str]]:
    """Recursive H2→H3 sub-split of oversized sections; parent `# Title`
    prepended so sub-pages remain self-contained. A `# Changelog` section
    with ≥2 `<Update label=...>` releases splits on those instead — its
    internal H2s repeat every release and carry no real identity."""
    expanded: list[tuple[str, str, str]] = []
    n_expanded = 0
    n_added_subsections = 0
    for s, b, sp in writes:
        h1_match = patterns.H1_PREFIX_RE.match(b)
        h1_prefix = (h1_match.group(1) + "\n\n") if h1_match else ""
        h1_text = h1_match.group(1)[2:].strip() if h1_match else ""
        sub_pages = None
        oversized = len(b.encode("utf-8")) > params.SPLIT_MAX_SECTION_BYTES
        if oversized and _looks_like_changelog(h1_text, b):
            sub_pages = _split_changelog_releases(s, b, sp, h1_prefix)
        if sub_pages is None:
            sub_pages = _size_aware_recursive_split(
                s, b, sp, h1_prefix = h1_prefix, levels = (2, 3),
            )
            if _is_fragmented_changelog(h1_text, sub_pages):
                sub_pages = [(s, b, sp)]
        if len(sub_pages) > 1:
            expanded.extend(sub_pages)
            n_expanded += 1
            n_added_subsections += len(sub_pages)
        else:
            expanded.append((s, b, sp))
    if n_expanded:
        delta = n_added_subsections - n_expanded
        logger.info(
            f"[post] h2-subsplit: {n_expanded} oversized section(s) "
            f"(> {params.SPLIT_MAX_SECTION_BYTES // 1024} KB) expanded into "
            f"{n_added_subsections} sub-pages (+{delta} net entries)"
        )
    return expanded


def _without_trailing_rule(body: str) -> str:
    """The section without the `---` that separated it from the next one (131 of Playwright's split API pages ended on one).
    Only the last few hundred characters are looked at."""
    cut = max(0, len(body) - 400)
    tail = body[cut:].rstrip()
    stripped = patterns.TRAILING_RULE_RE.sub("", tail)
    return body if stripped == tail else body[:cut] + stripped + "\n"


def _size_aware_recursive_split(
    slug: str,
    body: str,
    source_path: str,
    h1_prefix: str,
    levels: tuple[int, ...],
    max_bytes: int | None = None,
) -> list[tuple[str, str, str]]:
    """Sub-split only over the size cap; falls through on ORIGINAL body
    (not on stub-dropped fragments). `max_bytes` overrides
    SPLIT_MAX_SECTION_BYTES for callers with a smaller budget (the monolith
    path omits it and keeps the default)."""
    limit = max_bytes if max_bytes is not None else params.SPLIT_MAX_SECTION_BYTES
    if len(body.encode("utf-8")) <= limit:
        return [(slug, body, source_path)]
    if not levels:
        return [(slug, body, source_path)]
    level = levels[0]
    rest_levels = levels[1:]
    sub_sections = split_markdown_by_headings(body, split_levels = (level,))
    sub_writes: list[tuple[str, str]] = []
    for sub_h1, sub_h2, sub_b, _sp in sub_sections:
        heading = sub_h2 if sub_h2 else sub_h1
        if not heading:
            continue
        body_with_context = _without_trailing_rule(
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
        if len(sb.encode("utf-8")) >= params.SPLIT_MIN_SECTION_BYTES
    ]
    if len(sub_writes) < 2:
        return _size_aware_recursive_split(slug, body, source_path, h1_prefix, rest_levels, max_bytes = max_bytes)
    out: list[tuple[str, str, str]] = []
    for ss, sb in sub_writes:
        out.extend(_size_aware_recursive_split(ss, sb, source_path, h1_prefix, rest_levels, max_bytes = max_bytes))
    return out


def split_oversized_page(
    slug: str,
    body: str,
    max_bytes: int,
) -> list[tuple[str, str, str]]:
    """Split ONE oversized page from a multi-page corpus on H2/H3 (same
    atomic-fence machinery as the monolith path, parent H1 prepended for
    self-containment). Returns [(slug, body, "")] — single element, unsplit —
    when no clean split exists. Deliberately NOT the changelog path (Tier-1
    monolith's territory) and no stub/dup filtering (dedup already ran);
    slugs get hash-suffixed on collision like everywhere else here. Not H4: tried on Transformers' two children that stay
    over the cap (`AutoConfig`, 139 KB, `Generic model classes`, 273 KB), it cut off a few 500-byte method stubs and left
    the one `from_pretrained` docstring that holds the size at 138 KB and 170 KB — no gain for the fragments it adds."""
    h1_match = patterns.H1_PREFIX_RE.match(body)
    h1_prefix = (h1_match.group(1) + "\n\n") if h1_match else ""
    out = _size_aware_recursive_split(
        slug, body, "", h1_prefix = h1_prefix, levels = (2, 3),
        max_bytes = max_bytes,
    )
    # A heading that occurs twice in the page (Diffusers' LoRA page documents `LoraBaseMixin` in two places) gives two
    # identical sections — and `dedup_pages` already ran, before the split could make them.
    seen: set[str] = set()
    distinct: list[tuple[str, str, str]] = []
    for section in out:
        digest = hashlib.sha256(section[1].strip().encode("utf-8")).hexdigest()
        if digest not in seen:
            seen.add(digest)
            distinct.append(section)
    out = distinct
    if len(out) <= 1:
        return [(slug, body, "")]
    return _disambiguate_duplicate_slugs(out)


def dedup_pages(
    pages: list[tuple[str, str, str]],
) -> tuple[list[tuple[str, str, str]], int, int]:
    """[(slug, url, body), ...] → (filtered, stubs, dupes). Same rules as
    the monolith split's cleanup; each page was fetched on its own, so a short one with a code example is kept."""
    pre = len(pages)
    kept = [(s, u, b) for s, u, b in pages if worth_keeping(b, delimited = True)]
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
