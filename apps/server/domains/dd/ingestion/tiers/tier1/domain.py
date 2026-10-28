"""Tier 1 — pure helpers (manifest detection, HTML detection, path filter, host slug)."""
from __future__ import annotations
import domains
from . import params, patterns



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
