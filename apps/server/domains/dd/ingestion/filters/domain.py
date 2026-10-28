from __future__ import annotations
from . import keys, params, patterns

import fnmatch
import re
from urllib.parse import urlparse



def is_polyglot(framework_name: str) -> bool:
    return (framework_name or "").strip().lower() in keys.POLYGLOT_FRAMEWORKS


def build_language_filter(language: str | None) -> tuple[list[str], list[str]]:
    """(allow, deny) globs for `language`. Other-language slugs ≤2 chars are
    dropped from the deny list — `js`/`go` alone match too much else."""
    if not language:
        return [], list(params.DEFAULT_DENY_PATTERNS)
    key = language.strip().lower()
    target = keys.LANGUAGE_PATH_MAP.get(key, [key])
    other_slugs = [
        slug
        for k, slugs in keys.LANGUAGE_PATH_MAP.items()
        if k != key
        for slug in slugs
        if len(slug) >= params.LANGUAGE_SLUG_MIN_CHARS
    ]
    allow = [
        *params.LANGUAGE_ALLOW_ALWAYS,
        *[f"*/{s}/*"  for s in target],
        *[f"*/{s}-*/*" for s in target],
    ]
    deny = [
        *params.DEFAULT_DENY_PATTERNS,
        *[f"*/{s}/*" for s in other_slugs],
    ]
    return allow, deny


def should_keep(url: str, allow: list[str], deny: list[str]) -> bool:
    """Any allow match passes; otherwise pass when no deny matches."""
    if any(fnmatch.fnmatch(url, p) for p in deny):
        return False
    if allow:
        return any(fnmatch.fnmatch(url, p) for p in allow)
    return True


def passes_language(
    url: str,
    *,
    polyglot: bool,
    language: str | None,
    allow: list[str],
    deny: list[str],
) -> bool:
    """Whether a URL is in the language the docs are read in: judged against the `allow` / `deny` globs
    (`build_language_filter`) when the framework has several language editions and a language was asked for, or when there
    are globs at all; otherwise there is nothing to judge by and it passes."""
    if (polyglot and language) or allow or deny:
        return should_keep(url, allow, deny)
    return True


def same_host(url: str, host: str) -> bool:
    return (urlparse(url).netloc or "").lower() == host.lower()


def declared_filter(path_filter: dict | None) -> dict | None:
    """The catalog's own `include` / `exclude` and nothing else — the default noise filter stays off — or None when the entry
    declares neither. For a source that is already the framework's own list of pages (a bundle's `Source:` URLs, an llms.txt),
    which must never be trimmed on the crawler's authority."""
    if not path_filter or not (path_filter.get("include") or path_filter.get("exclude")):
        return None
    return {**path_filter, "disable_defaults": True}


def passes_path_filter(url: str, catalog_filter: dict | None = None) -> bool:
    """Path-pattern filter; `catalog_filter` keys: `include`, `exclude`,
    `disable_defaults` (all optional). Invalid user regexes silently skipped
    so a catalog typo doesn't blanket-reject URLs."""
    path = urlparse(url).path or "/"
    filt = catalog_filter or {}
    if not filt.get("disable_defaults") and patterns.DEFAULT_EXCLUDE_RE.search(path):
        return False
    if _any_match(filt.get("exclude") or [], path):
        return False
    include = filt.get("include") or []
    return _any_match(include, path) if include else True


def _any_match(regexes: list[str], path: str) -> bool:
    """Whether `path` matches any of the catalog's regexes (case-insensitive); one that is not a valid regex is skipped."""
    for pat in regexes:
        try:
            if re.search(pat, path, re.IGNORECASE):
                return True
        except re.error:
            continue
    return False
