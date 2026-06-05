from __future__ import annotations

import fnmatch
import re
from urllib.parse import urlparse

from .keys import LANGUAGE_PATH_MAP, POLYGLOT_FRAMEWORKS
from .params import DEFAULT_DENY_PATTERNS
from .patterns import DEFAULT_EXCLUDE_RE


def is_polyglot(framework_name: str) -> bool:
    return (framework_name or "").strip().lower() in POLYGLOT_FRAMEWORKS


def build_language_filter(language: str | None) -> tuple[list[str], list[str]]:
    """(allow, deny) globs for `language`. Other-language slugs ≤2 chars are
    dropped from the deny list — `js`/`go` alone match too much else."""
    if not language:
        return [], list(DEFAULT_DENY_PATTERNS)
    key = language.strip().lower()
    target = LANGUAGE_PATH_MAP.get(key, [key])
    other_slugs = [
        slug
        for k, slugs in LANGUAGE_PATH_MAP.items()
        if k != key
        for slug in slugs
        if len(slug) > 2
    ]
    allow = [
        "*concept*", "*specification*", "*spec*", "*overview*",
        *[f"*/{s}/*"  for s in target],
        *[f"*/{s}-*/*" for s in target],
    ]
    deny = [
        *DEFAULT_DENY_PATTERNS,
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


def same_host(url: str, host: str) -> bool:
    return (urlparse(url).netloc or "").lower() == host.lower()


def passes_path_filter(url: str, catalog_filter: dict | None = None) -> bool:
    """Path-pattern filter; `catalog_filter` keys: `include`, `exclude`,
    `disable_defaults` (all optional). Invalid user regexes silently skipped
    so a catalog typo doesn't blanket-reject URLs."""
    path = urlparse(url).path or "/"
    filt = catalog_filter or {}
    if not filt.get("disable_defaults") and DEFAULT_EXCLUDE_RE.search(path):
        return False
    for pat in filt.get("exclude") or []:
        try:
            if re.search(pat, path, re.IGNORECASE):
                return False
        except re.error:
            continue
    include = filt.get("include") or []
    if include:
        for pat in include:
            try:
                if re.search(pat, path, re.IGNORECASE):
                    return True
            except re.error:
                continue
        return False
    return True
