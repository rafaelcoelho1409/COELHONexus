"""Tier 2 — pure helpers (index parse + slug + markdown-response detect + what an index is worth)."""
from __future__ import annotations
import domains
from . import params, patterns

from typing import Literal
from urllib.parse import urljoin, urlparse

import httpx



def covers_site(listed: int, site_pages: int) -> bool:
    """Whether an index listing `listed` pages is a fair index of a site whose sitemap has `site_pages`. Only a site
    worth judging is judged (see COVERAGE_MIN/MAX_SITEMAP_PAGES): anything else gives the index the benefit of the doubt."""
    if not params.COVERAGE_MIN_SITEMAP_PAGES <= site_pages <= params.COVERAGE_MAX_SITEMAP_PAGES:
        return True
    return listed / site_pages >= params.COVERAGE_MIN_RATIO


def serves_markdown(links: list[tuple[str, str, str, str]]) -> bool:
    """Whether the index's pages are markdown files (`…/page.md`): a site that publishes those for what it lists most
    likely publishes them for the rest of its pages too (Qdrant: `…/search-patterns/` → `…/search-patterns.md`)."""
    if not links:
        return False
    markdown = sum(1 for _title, url, _section, _notes in links if urlparse(url).path.lower().endswith(params.MARKDOWN_SUFFIX))
    return markdown / len(links) >= params.MARKDOWN_INDEX_MIN_SHARE


def is_stub(body: str) -> bool:
    """A fetched page too small to study from: under STUB_MIN_BYTES, unless it holds a code example."""
    return not domains.dd.ingestion.post.domain.worth_keeping(body, delimited = True, floor = params.STUB_MIN_BYTES)


def drop_single_section(links: list[tuple[str, str, str, str]]) -> list[tuple[str, str, str, str]]:
    """The index's `##` headings become the pages' sections (the explorer groups by them) only when there are at least
    NAV_MIN_SECTIONS of them: one heading over every page — Hugging Face's "Docs" over 771 of them — groups nothing, and
    would stand in the sidebar for the folders the URLs show."""
    if len({section for _title, _url, section, _notes in links if section}) >= domains.dd.ingestion.post.params.NAV_MIN_SECTIONS:
        return links
    return [(title, url, "", notes) for title, url, _section, notes in links]


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
        # Cross-host: static raw content only. Never HTML pages — GitHub repo / PyPI project meta-links stay dropped.
        if base_host and host and host != base_host and not urlparse(full).path.lower().endswith(params.CROSS_HOST_SUFFIXES):
            return
        if full in seen:
            return
        seen.add(full)
        out.append((title.strip(), full, section, (notes or "").strip()))

    for line in (body or "").splitlines():
        h2 = patterns.H2_RE.match(line)
        if h2:
            section = h2.group(1).strip()[: params.SECTION_MAX_CHARS]
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
    return " ".join(lines)[: params.SUMMARY_MAX_CHARS]


def slugify(s: str) -> str:
    return patterns.NON_SLUG_RE.sub("-", s.lower()).strip("-")[: params.SLUG_MAX_CHARS] or params.SLUG_FALLBACK


def page_slug(title: str, url: str) -> str:
    """A page's slug: its title's, else its URL path's."""
    return slugify(title or urlparse(url).path)


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
    m = patterns.FILENAME_H1_RE.match(lines[first].strip())
    if not m:
        return body
    following = [l for l in lines[first + 1 :] if l.strip()][: params.FILENAME_H1_LOOKAHEAD_LINES]
    if not any(l.strip().startswith("#") for l in following):
        return body
    out = lines[:first] + lines[first + 1 :]
    nxt = next((i for i, l in enumerate(out) if l.strip()), None)
    if nxt is not None and out[nxt].strip() in params.FILENAME_H1_SEPARATORS:
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
    if len(b.encode("utf-8")) >= params.REDIRECT_STUB_MAX_BYTES:
        return False
    if b.count("```") > 0:
        return False
    return any(rx.search(b) is not None for rx in patterns.REDIRECT_STUB_RES)


def is_markdown_response(resp: httpx.Response) -> bool:
    ctype = (resp.headers.get("content-type") or "").lower()
    return (
        any(markdown_type in ctype for markdown_type in params.MARKDOWN_CONTENT_TYPES)
        or resp.url.path.endswith(params.MARKDOWN_SUFFIX)
    )


def body_kind(resp: httpx.Response) -> Literal["markdown", "text", "python", "html"]:
    """What a fetched page is, which decides how it becomes Markdown: markdown passes through, a plain-text index (`apilist.txt`)
    is kept verbatim so its function list survives intact, a raw example script is fenced as code, and HTML goes through the
    extractor."""
    if is_markdown_response(resp):
        return "markdown"
    path = resp.url.path.lower()
    if path.endswith(params.TEXT_SUFFIX):
        return "text"
    if path.endswith(params.PYTHON_SUFFIX):
        return "python"
    return "html"


def fence_python(source: str) -> str:
    """An example script as a Python code block, so vault fence extraction and the digest's code references see real code
    instead of flat prose."""
    return f"```python\n{(source or '').strip()}\n```\n"


def filter_links(
    links: list[tuple[str, str, str, str]], path_filter: dict | None,
) -> tuple[list[tuple[str, str, str, str]], int] | None:
    """`(links inside the catalog's path_filter, how many were listed)`, or None when the entry declares no filter. Only the
    filter the catalog declares counts — the default noise filter stays off, so an index is never trimmed on the crawler's
    own authority."""
    only_declared = domains.dd.ingestion.filters.domain.declared_filter(path_filter)
    if only_declared is None:
        return None
    kept = [link for link in links if domains.dd.ingestion.filters.domain.passes_path_filter(link[1], only_declared)]
    return kept, len(links)


def slug_stem(link: str, base: str) -> str:
    """What tells a page from the earlier one that took the slug `base`: the nearest folder of its URL that is not `base` itself
    (`…/private-cloud/configuration/` → `private-cloud`, not the `configuration` folder), else the first label of its host. A
    trailing file segment (`index.md`, `foo.md`) is left out: its name re-slugifies to `base` and gives a redundant `slug-slug`."""
    try:
        parts = [p for p in urlparse(link).path.split("/") if p]
        if parts and parts[-1].lower().endswith(params.MARKDOWN_SUFFIX):
            parts = parts[:-1]
        for segment in reversed(parts):
            candidate = slugify(segment)
            if candidate and candidate != base:
                return candidate
        return slugify((urlparse(link).netloc or params.SLUG_STEM_FALLBACK).split(".")[0]) or params.SLUG_STEM_FALLBACK
    except Exception:  # noqa: BLE001
        return params.SLUG_STEM_FALLBACK


def free_slug(owners: dict[str, str], base: str, link: str) -> str:
    """The slug `link` takes in a run where `owners` ({slug: the link that has it}) is already spoken for: the bare `base` when
    nobody else has it, else `base` with the stem that tells the page apart, a counter as the last resort. The same input gives
    the same slug, so nothing is silently overwritten. Keys were already unique (idx-prefixed), so this only cleans manifests
    and explorer titles — no storage layout change."""
    if base not in owners or owners[base] == link:
        return base
    stem = slug_stem(link, base)
    suffixed = domains.dd.ingestion.tiers.tier3.domain.suffixed_slug
    candidate = suffixed(base, stem)
    i = 2
    while candidate in owners and owners[candidate] != link:
        candidate = suffixed(base, f"{stem}-{i}")
        i += 1
    return candidate
