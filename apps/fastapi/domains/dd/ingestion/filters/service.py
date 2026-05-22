"""URL & language filter functions shared across Tier 3 / Tier 4."""
import fnmatch
import re
from urllib.parse import urlparse

from .constants import (
    DEFAULT_DENY_PATTERNS,
    LANGUAGE_PATH_MAP,
    POLYGLOT_FRAMEWORKS,
    _DEFAULT_EXCLUDE_RE,
)


def is_polyglot(framework_name: str) -> bool:
    return (framework_name or "").strip().lower() in POLYGLOT_FRAMEWORKS


def build_language_filter(
    language: str | None,
) -> tuple[list[str], list[str]]:
    """Return (allow, deny) glob lists for the given language.

    No language → empty allow (don't over-restrict on unusual path layouts)
    + the standard deny list.

    Specific language → allow only that language's slugs (plus a small set
    of language-agnostic ones) + deny all other languages' slugs.
    """
    if not language:
        return [], list(DEFAULT_DENY_PATTERNS)

    key = language.strip().lower()
    target = LANGUAGE_PATH_MAP.get(key, [key])

    # Drop 2-char slugs from the *other* languages' deny list — "js" and
    # "go" alone match too much else (e.g. javascript samples inside a
    # Python project).
    other_slugs = [
        slug
        for k, slugs in LANGUAGE_PATH_MAP.items()
        if k != key
        for slug in slugs
        if len(slug) > 2
    ]

    allow = [
        "*concept*", "*specification*", "*spec*", "*overview*",
        *[f"*/{s}/*" for s in target],
        *[f"*/{s}-*/*" for s in target],
    ]
    deny = [
        *DEFAULT_DENY_PATTERNS,
        *[f"*/{s}/*" for s in other_slugs],
    ]
    return allow, deny


def should_keep(
    url: str,
    allow: list[str],
    deny: list[str],
) -> bool:
    """fnmatch glob test — pass any explicit allow first; otherwise
    pass when nothing in deny matches.
    """
    if any(fnmatch.fnmatch(url, p) for p in deny):
        return False
    if allow:
        return any(fnmatch.fnmatch(url, p) for p in allow)
    return True


def same_host(url: str, host: str) -> bool:
    return (urlparse(url).netloc or "").lower() == host.lower()


def passes_path_filter(
    url: str,
    catalog_filter: dict | None = None,
) -> bool:
    """Return True if the URL passes the path-pattern filter.

    catalog_filter shape (all keys optional, all values lists of regex strings):
        {
          "include":         [...],   # if non-empty, URL MUST match at least one
          "exclude":         [...],   # URL must match NONE
          "disable_defaults": bool,    # if true, skip DEFAULT_EXCLUDE_PATH_PATTERNS
        }
    """
    path = urlparse(url).path or "/"
    filt = catalog_filter or {}
    # Defaults apply unless explicitly disabled.
    if not filt.get("disable_defaults") and _DEFAULT_EXCLUDE_RE.search(path):
        return False
    extra_exclude = filt.get("exclude") or []
    for pat in extra_exclude:
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
