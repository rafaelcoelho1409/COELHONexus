"""Tier 1 — pure helpers (manifest detection, HTML detection, path filter, host slug, and the bundle → pages transform)."""
from __future__ import annotations
import domains
from . import params, patterns

import logging
from urllib.parse import urljoin, urlparse


logger = logging.getLogger(__name__)


def looks_like_manifest(body: str) -> tuple[bool, dict]:
    fence_count = len(patterns.FENCE_RE.findall(body))
    url_count = len(patterns.URL_LINE_RE.findall(body))
    md_pointer_count = len(patterns.MD_POINTER_RE.findall(body))
    is_manifest = (
        fence_count < params.MANIFEST_MAX_FENCES
        and (url_count > params.MANIFEST_MIN_URL_LINES
             or md_pointer_count > params.MANIFEST_MIN_URL_LINES)
    )
    return is_manifest, {
        "fences": fence_count,
        "urls": url_count,
        "md_pointers": md_pointer_count,
    }


def apply_path_filter(body: str, path_filter: dict | None) -> tuple[str, int, int]:
    """Keep only the sections of a bundle whose `Source:` URL passes the catalog's `path_filter` → (body, kept, total).

    For a docs site whose single bundle covers more than one product (Browser Use: 177 Cloud pages around 56 of the
    open-source library the catalog is after). Only the filter the catalog declares counts — the default noise filter
    stays off, so a bundle is never trimmed on its own authority. A bundle whose sections do not carry `Source:` URLs
    cannot be told apart by path and comes back whole (`kept == total == 0`), as does an entry with no filter."""
    only_declared = domains.dd.ingestion.filters.domain.declared_filter(path_filter)
    if only_declared is None:
        return body, 0, 0
    sections = domains.dd.ingestion.post.domain.split_by_source_markers(body)
    if sections is None:
        return body, 0, 0
    kept = [
        text
        for _h1, _h2, text, source_url in sections
        if source_url and domains.dd.ingestion.filters.domain.passes_path_filter(source_url, only_declared)
    ]
    return "".join(kept), len(kept), len(sections)


def nav_links(nav_text: str, nav_url: str) -> list[tuple[str, str, str]]:
    """`[(title, url, section), ...]` of the pages the site's own index (its `llms.txt`, `nav_text`) lists, in the order it lists them —
    its navigation. Best effort: no index, or one that cannot be read, gives `[]` and the bundle's own order stands."""
    if not nav_text:
        return []
    try:
        links = domains.dd.ingestion.tiers.tier2.domain.parse_index(nav_text, nav_url)
    except Exception as e:  # noqa: BLE001 — the index is a nicety, never a reason to fail a run
        logger.info(f"[tier-1] no navigation order from {nav_url}: {type(e).__name__}: {e}")
        return []
    return [(title, url, section) for title, url, section, _notes in links]


def nav_index(nav_text: str, nav_url: str) -> list[tuple[str, str]]:
    """`[(url, section), ...]` — `nav_links` without the titles: what the split follows for the pages' order and sections."""
    return [(url, section) for _title, url, section in nav_links(nav_text, nav_url)]


def pages_from_bundle(
    body: str,
    *,
    url: str,
    nav_index: list[tuple[str, str]],
) -> list[domains.dd.ingestion.transform.entities.Page]:
    """What Tier 1 makes of a bundle (`body`, already cut to the catalog's `path_filter`; `url` is where it came from, `nav_index` the
    site's own page order and sections): its pages, as the explorer and the Planner read them. The whole chain in one place and pure —
    the title of a bundle that stays one page, the normalizer, links made absolute, the split, the site's navigation order and
    sections, titles told apart — the same steps, in the same order, as `Store.add_page` and `post.service.apply_to_store` take when
    a run stores the bundle (arm A of the A/B). Image downloads are not part of it: they come after, for every arm alike."""
    post = domains.dd.ingestion.post
    entities = domains.dd.ingestion.transform.entities
    slug = host_slug((urlparse(url).netloc or "").lower())
    will_split = post.domain.will_be_split(body)
    title = post.domain.display_title(body, slug)
    try:
        normalized = domains.dd.synth.nodes.corpus_normalize.domain.normalize_doc(body).body
    except Exception as e:  # noqa: BLE001 — the page is stored as it came, as `Store.add_page` does
        logger.warning(f"[tier-1] normalize_doc failed for {slug!r}: {type(e).__name__}: {e}; keeping the bundle as it came")
        normalized = body
    text = post.domain.absolutize_links(normalized, url, relative = not will_split)
    whole = [entities.Page(slug = slug, title = title, body = text)]
    if len(text.encode("utf-8")) < post.params.MONOLITH_SPLIT_THRESHOLD_BYTES:
        return whole
    writes, _stubs, _dupes = post.domain.split_monolith(text, slug)
    if len(writes) == 1 and writes[0][1] == text:
        return whole
    index = nav_index if will_split else []
    writes = post.domain.order_by_nav(writes, [page_url for page_url, _section in index])
    sections = post.domain.sections_by_nav(writes, index)
    titles = post.domain.strip_site_suffix([post.domain.display_title(page_body, page_slug) for page_slug, page_body, _source in writes])
    titles = post.domain.disambiguate_titles([(t, source or url) for t, (_slug, _body, source) in zip(titles, writes)])
    return [
        entities.Page(
            slug = page_slug, title = t, body = page_body, section = section, source_url = source,
            tier = post.params.CHANGELOG_TIER if post.patterns.CHANGELOG_RELEASE_MARKER in page_slug else "",
        )
        for (page_slug, page_body, source), t, section in zip(writes, titles, sections)
    ]


def next_part_url(body: str, current_url: str) -> str | None:
    """Where the next part of a paginated bundle is, when `body` (a part, as the site sent it) names one and it is on the same host as
    the part itself; `None` for a bundle that is whole, or whose pointer leaves the site."""
    found = patterns.NEXT_PART_RE.search(body[: params.PART_BANNER_SNIFF_CHARS])
    if found is None:
        return None
    target = urljoin(current_url, found.group(1).strip())
    if (urlparse(target).netloc or "").lower() != (urlparse(current_url).netloc or "").lower():
        return None
    return target


def part_file_name(number: int) -> str:
    """The capture's name for the `number`th part of a bundle (the first is `BUNDLE_FILE`)."""
    return params.BUNDLE_FILE if number == 1 else params.PART_FILE.format(n = number)


def join_parts(parts: list[str]) -> str:
    """One bundle from the parts of a paginated one, in order. Each part opens with the same pagination banner ("part 2 of 2 …
    Previous part: …"), which says nothing about the docs and would sit in the middle of the previous part's last page: the banner of
    every part after the first is taken off (the first part's stays, as it always has)."""
    if not parts:
        return ""
    text = parts[0]
    for part in parts[1:]:
        banner = patterns.PART_BANNER_RE.match(part)
        if banner is not None and patterns.PART_BANNER_MARK_RE.search(banner.group(0)):
            part = part[banner.end():]
        text = (text if text.endswith("\n") else text + "\n") + part
    return text


def unusable_reason(status: int, body: str, *, min_bytes: int = params.MIN_OK_BYTES) -> str | None:
    """Why a response cannot be a bundle (not a 200, too short, an HTML page), or `None` when it can."""
    if status != 200:
        return f"HTTP {status}"
    if len(body) < min_bytes:
        return f"body too short ({len(body)}B)"
    if looks_like_html(body):
        return "HTML page instead of a markdown bundle"
    return None


def looks_like_html(body: str) -> bool:
    """True for an HTML document: a site that answers 200 with its error page, a login wall or a bot challenge for a
    URL that should hold markdown. Only the start is looked at — a real bundle may well quote HTML further down."""
    return bool(patterns.HTML_START_RE.match(body[: params.HTML_SNIFF_CHARS]))


def retry_after_seconds(header: str | None) -> float | None:
    """A `Retry-After` given in seconds (the HTTP-date form is ignored), bounded by what a single bundle's fetch is
    willing to wait."""
    return domains.dd.ingestion.tiers.fetch.domain.retry_after_seconds(header, cap = params.RETRY_AFTER_MAX_S)


def host_slug(host: str) -> str:
    s = patterns.NON_SLUG_RE.sub("-", host.lower()).strip("-")
    return f"{s}{params.HOST_SLUG_SUFFIX}"[: params.SLUG_MAX_CHARS] or params.HOST_SLUG_SUFFIX.lstrip("-")
