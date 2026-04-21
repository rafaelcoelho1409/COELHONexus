"""
Knowledge Distiller — Package Registry Existence Check

Existence-only probe that answers two questions per framework:
  1. Does this package exist in any registry we know about?
  2. What homepage / repo URL did the publisher declare?

Both are PASSED AS EVIDENCE to the LLM rerank stage (Stage C) — the resolver
does NOT derive docs URLs from registries directly. Publishers version their
docs sites with incompatible conventions (e.g. Airflow uses
`/docs/apache-airflow/3.0/`, others use `/latest/`, `/stable/`, `/v2/`…), so
maintaining a hard-coded per-framework map is the wrong abstraction. The LLM
reads candidate URLs surfaced by SearXNG and picks the version-matching one.

Return shape (`RegistryHint`, defined in schemas.knowledge.resolver):
  { exists, homepage, repo, latest_version, all_versions, source }

`exists=False` is returned when every registry probed returns 404 OR when
the registries are unreachable. The resolver then degrades to SearXNG-only
resolution without failing the request.

Zero auth by default, modest traffic, short timeouts. Safe to call
synchronously from FastAPI handlers.
"""
import logging
import re
from typing import Optional
from urllib.parse import quote

import httpx

from schemas.knowledge.resolver import RegistryHint


logger = logging.getLogger(__name__)


_TIMEOUT_SECONDS = 5.0
_MAX_VERSIONS = 30
_USER_AGENT = "COELHONexus-KnowledgeDistiller/1.0"


# =============================================================================
# Public API — cross-ecosystem existence check
# =============================================================================
async def hint_lookup(
    framework: str,
    language: Optional[str] = None) -> RegistryHint:
    """
    Probe PyPI / npm / crates.io in language-hinted order and return the
    first hit as a RegistryHint. Any one success returns early; if all
    three miss, returns RegistryHint(exists=False).

    Network errors surface as exists=False (conservative — the resolver's
    degraded path uses SearXNG-only candidate generation).
    """
    lang = (language or "").strip().lower()
    if lang in ("python", "py"):
        order = [_pypi_hint, _npm_hint, _crates_hint]
    elif lang in ("javascript", "typescript", "js", "ts", "node", "nodejs"):
        order = [_npm_hint, _pypi_hint, _crates_hint]
    elif lang in ("rust", "rs"):
        order = [_crates_hint, _pypi_hint, _npm_hint]
    else:
        order = [_pypi_hint, _npm_hint, _crates_hint]
    for probe in order:
        try:
            hint = await probe(framework)
            if hint and hint.exists:
                return hint
        except Exception as e:
            logger.info(f"[registry] {probe.__name__} probe failed for {framework!r}: {e}")
            continue
    return RegistryHint(exists = False)


# =============================================================================
# PyPI — Python
# =============================================================================
def _pypi_sort_key(v: str) -> tuple:
    """
    Best-effort PEP-440-ish sort key — enough to get numeric-major-first
    ordering right for the vast majority of packages. Not a full parser.
    """
    parts = re.split(r"[.\-+]", v)
    key = []
    for p in parts:
        m = re.match(r"^(\d+)(.*)", p)
        if m:
            key.append((int(m.group(1)), m.group(2) or ""))
        else:
            key.append((-1, p))
    return tuple(key)


async def _pypi_hint(framework: str) -> Optional[RegistryHint]:
    # PyPI normalizes package names to lowercase with hyphens (PEP 503)
    norm = re.sub(r"[-_.]+", "-", framework.lower())
    url = f"https://pypi.org/pypi/{norm}/json"
    async with httpx.AsyncClient(
        timeout = _TIMEOUT_SECONDS,
        headers = {"User-Agent": _USER_AGENT},
    ) as c:
        r = await c.get(url)
    if r.status_code == 404:
        return None
    r.raise_for_status()
    data = r.json()
    info = data.get("info") or {}
    releases = data.get("releases") or {}
    live = [v for v, files in releases.items() if files]
    live.sort(key = _pypi_sort_key, reverse = True)
    # `project_urls` is where publishers put Homepage/Documentation/Source.
    # Prefer explicit Documentation link; fall back to Homepage.
    project_urls = info.get("project_urls") or {}
    homepage = (
        project_urls.get("Documentation")
        or project_urls.get("documentation")
        or info.get("home_page")
        or project_urls.get("Homepage")
    )
    repo = (
        project_urls.get("Source")
        or project_urls.get("Source Code")
        or project_urls.get("Repository")
        or project_urls.get("GitHub")
    )
    return RegistryHint(
        exists = True,
        homepage = homepage or None,
        repo = repo or None,
        latest_version = info.get("version"),
        all_versions = live[:_MAX_VERSIONS],
        source = "pypi",
    )


# =============================================================================
# npm — JavaScript / TypeScript
# =============================================================================
async def _npm_hint(framework: str) -> Optional[RegistryHint]:
    # npm accepts scoped packages (`@scope/name`) — URL-encode the slash.
    encoded = quote(framework, safe = "@")
    url = f"https://registry.npmjs.org/{encoded}"
    async with httpx.AsyncClient(
        timeout = _TIMEOUT_SECONDS,
        headers = {"User-Agent": _USER_AGENT},
    ) as c:
        r = await c.get(url)
    if r.status_code == 404:
        return None
    r.raise_for_status()
    data = r.json()
    versions = list((data.get("versions") or {}).keys())
    versions.sort(key = _pypi_sort_key, reverse = True)
    dist_tags = data.get("dist-tags") or {}
    latest = dist_tags.get("latest")
    latest_ver_block = (data.get("versions") or {}).get(latest or "", {})
    homepage = (
        data.get("homepage")
        or latest_ver_block.get("homepage")
    )
    repo_raw = data.get("repository") or latest_ver_block.get("repository")
    if isinstance(repo_raw, dict):
        repo = repo_raw.get("url")
    elif isinstance(repo_raw, str):
        repo = repo_raw
    else:
        repo = None
    return RegistryHint(
        exists = True,
        homepage = homepage or None,
        repo = repo or None,
        latest_version = latest,
        all_versions = versions[:_MAX_VERSIONS],
        source = "npm",
    )


# =============================================================================
# crates.io — Rust
# =============================================================================
async def _crates_hint(framework: str) -> Optional[RegistryHint]:
    norm = framework.lower().replace(" ", "-").replace("_", "-")
    url = f"https://crates.io/api/v1/crates/{norm}"
    async with httpx.AsyncClient(
        timeout = _TIMEOUT_SECONDS,
        headers = {"User-Agent": _USER_AGENT},
    ) as c:
        r = await c.get(url)
    if r.status_code == 404:
        return None
    r.raise_for_status()
    data = r.json()
    crate = data.get("crate") or {}
    versions = [v["num"] for v in (data.get("versions") or []) if not v.get("yanked")]
    return RegistryHint(
        exists = True,
        homepage = crate.get("documentation") or crate.get("homepage"),
        repo = crate.get("repository"),
        latest_version = crate.get("max_stable_version") or crate.get("max_version"),
        all_versions = versions[:_MAX_VERSIONS],
        source = "crates.io",
    )
