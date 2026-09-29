"""Tier 2 — pure helpers (index parse + slug + markdown-response detect)."""
from __future__ import annotations
from . import patterns

import re
from urllib.parse import urljoin, urlparse

import httpx



def parse_index(body: str, base_url: str) -> list[tuple[str, str, str, str]]:
    """[(title, url, section, notes), ...] from llms.txt (AnswerDotAI spec v2).

    Single line-scan in document order (MD `[t](u)` + bare `t: u` styles share
    one pass, unlike the old two-pass finditer which grouped all MD links
    before all bare links). `section` is the enclosing `##` heading ("" before
    the first one); `notes` is the optional `: ...` suffix. Same-host filter
    drops GitHub/PyPI meta-links. Preserves first-occurrence order."""
    base_host = (urlparse(base_url).netloc or "").lower()
    out: list[tuple[str, str, str, str]] = []
    seen: set[str] = set()
    section = ""

    def _add(title: str, url: str, notes: str) -> None:
        full = urljoin(base_url, url.strip())
        host = (urlparse(full).netloc or "").lower()
        if base_host and host and host != base_host:
            # Cross-host: static raw content only (FastHTML curates an HTMX
            # `reference.md`, Starlette `.md` guides, `.py` examples, and a
            # MonsterUI `apilist.txt` off-host). Never HTML pages — GitHub
            # repo / PyPI project meta-links stay dropped.
            if not urlparse(full).path.lower().endswith((".md", ".txt", ".py")):
                return
        if full in seen:
            return
        seen.add(full)
        out.append((title.strip(), full, section, (notes or "").strip()))

    for line in (body or "").splitlines():
        h2 = patterns.H2_RE.match(line)
        if h2:
            section = h2.group(1).strip()[:120]
            continue
        m = patterns.LINK_MD_RE.match(line)
        if m:
            _add(m.group(1), m.group(2), m.group(3) or "")
            continue
        b = patterns.LINK_BARE_RE.match(line)
        if b:
            _add(b.group(1), b.group(2), "")
    return out


def parse_index_summary(body: str) -> str:
    """Author-written `>` blockquote summary near the top of the index (spec
    v2 puts it right after the H1; Qdrant nests its under a leading
    `## Overall Summary` section instead — both shapes captured). Stops at the
    first file-list link. "" when absent — callers must treat "" as
    'no summary', never as content."""
    lines: list[str] = []
    for line in (body or "").splitlines():
        if patterns.LINK_MD_RE.match(line) or patterns.LINK_BARE_RE.match(line):
            break
        q = patterns.QUOTE_RE.match(line)
        if q and q.group(1).strip():
            lines.append(q.group(1).strip())
            continue
        s = line.strip()
        if not s or s.startswith("#"):
            continue
        if lines:
            break
    return " ".join(lines)[:2000]


def slugify(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")[:80] or "page"


def strip_filename_h1(body: str, url: str) -> str:
    """Drop a filename-shaped debris H1 opening a gist/raw mirror (observed
    live 2026-09-29: `starlette-sml.md` opens with a literal `# index.md`
    line — the mirror's display name, not a real section — followed by the
    actual `# Starlette Introduction` title).

    Fires only when ALL hold: the first non-empty line is an H1 whose text
    looks like a filename (`xxx.md`/`.txt`/`.rst`, so a real title such as
    `# Installation` can never match), AND another heading follows within 6
    non-empty lines (the real title — a lone filename-heading page keeps its
    only heading). A bare `---` separator left directly behind the removed
    line goes with it. Everything else passes through byte-identical."""
    lines = (body or "").splitlines(keepends = True)
    first = next((i for i, l in enumerate(lines) if l.strip()), None)
    if first is None:
        return body
    m = re.match(r"^#\s+([\w\-. ]+\.(?:md|markdown|txt|rst))\s*$", lines[first].strip())
    if not m:
        return body
    following = [l for l in lines[first + 1 :] if l.strip()][:6]
    if not any(l.strip().startswith("#") for l in following):
        return body
    out = lines[:first] + lines[first + 1 :]
    nxt = next((i for i, l in enumerate(out) if l.strip()), None)
    if nxt is not None and out[nxt].strip() in ("---", "..."):
        out = out[:nxt] + out[nxt + 1 :]
    return "".join(out)


def looks_like_redirect_stub(body: str) -> bool:
    """True when a fetched page is only a pointer elsewhere (archived-version
    redirect stub, moved-page placeholder) — mirror of Tier 1's
    `looks_like_manifest` shape guard, at page granularity.

    Conjunctive on purpose (same philosophy as Tier 1's fences-AND-urls):
    a marker phrase alone is not enough, since real prose can quote one in
    passing. Verified 2026-09-29 against all 120 sub-3KB Qdrant pages: fires
    exactly on the 1 archived stub, zero false positives on the other 119
    (real short tutorials keep their fences/headings)."""
    b = body or ""
    if len(b.encode("utf-8")) >= 2048:
        return False
    if b.count("```") > 0:
        return False
    return any(rx.search(b) is not None for rx in patterns.REDIRECT_STUB_RES)


def is_markdown_response(resp: httpx.Response) -> bool:
    ctype = (resp.headers.get("content-type") or "").lower()
    return (
        "text/markdown" in ctype
        or "text/x-markdown" in ctype
        or resp.url.path.endswith(".md")
    )
