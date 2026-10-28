"""Tier 5 — pure helpers (URL parse + blob filter + path slug)."""
from __future__ import annotations
from . import params, patterns

from urllib.parse import urlparse



def parse_repo(url: str) -> tuple[str, str] | None:
    """`https://github.com/org/repo[/...]` → (org, repo). Returns None on
    URLs that don't look like a github.com repo path."""
    p = urlparse(url)
    if (p.netloc or "").lower() not in params.GITHUB_HOSTS:
        return None
    parts = [s for s in (p.path or "").strip("/").split("/") if s]
    if len(parts) < 2:
        return None
    org, repo = parts[0], parts[1]
    return org, repo.removesuffix(params.GIT_SUFFIX)


def repo_api_url(org: str, repo: str) -> str:
    return f"{params.API_BASE}/repos/{org}/{repo}"


def tree_api_url(org: str, repo: str, branch: str, *, recursive: bool = True) -> str:
    """The listing of the repository's files at `branch` — every file in it, unless `recursive` is off (the form the progress
    record names it by)."""
    return f"{repo_api_url(org, repo)}/git/trees/{branch}" + ("?recursive=1" if recursive else "")


def raw_url(org: str, repo: str, branch: str, path: str) -> str:
    return f"{params.RAW_BASE}/{org}/{repo}/{branch}/{path}"


def blobs_of_tree(data: dict) -> list[tuple[str, int | None]]:
    """`[(path, size in bytes or None), ...]` of every file (blob) in a GitHub tree listing, in its order."""
    return [
        (node["path"], node.get("size"))
        for node in data.get("tree") or []
        if node.get("type") == "blob" and node.get("path")
    ]


def is_docs_blob(path: str) -> bool:
    if not path.lower().endswith(params.MD_EXTS):
        return False
    if any(path.startswith(p) for p in params.SKIP_PREFIXES):
        return False
    if any(s in path for s in params.SKIP_SUBSTRINGS):
        return False
    return not patterns.NON_EN_LOCALE_RE.search(path)


def slug_from_path(path: str) -> str:
    cleaned = patterns.MD_EXT_RE.sub("", path)
    cleaned = patterns.NON_ALNUM_RE.sub("-", cleaned.lower()).strip("-")
    return cleaned[: params.SLUG_MAX_CHARS] or params.SLUG_FALLBACK
