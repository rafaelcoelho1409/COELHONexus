"""Tier 5 — pure helpers (URL parse + blob filter + path slug)."""
from __future__ import annotations
from . import params, patterns

from urllib.parse import urlparse



def parse_repo(url: str) -> tuple[str, str] | None:
    """`https://github.com/org/repo[/...]` → (org, repo). Returns None on
    URLs that don't look like a github.com repo path."""
    p = urlparse(url)
    if (p.netloc or "").lower() not in ("github.com", "www.github.com"):
        return None
    parts = [s for s in (p.path or "").strip("/").split("/") if s]
    if len(parts) < 2:
        return None
    org, repo = parts[0], parts[1]
    if repo.endswith(".git"):
        repo = repo[:-4]
    return org, repo


def is_docs_blob(path: str) -> bool:
    if not path.lower().endswith(params.MD_EXTS):
        return False
    if any(path.startswith(p) for p in params.SKIP_PREFIXES):
        return False
    if any(s in path for s in params.SKIP_SUBSTRINGS):
        return False
    if patterns.NON_EN_LOCALE_RE.search(path):
        return False
    return True


def slug_from_path(path: str) -> str:
    cleaned = patterns.MD_EXT_RE.sub("", path)
    cleaned = patterns.NON_ALNUM_RE.sub("-", cleaned.lower()).strip("-")
    return cleaned[:120] or "readme"
