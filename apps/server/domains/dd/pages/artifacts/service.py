"""Artifact materialization: fetch (or decode) → MinIO write → HTML/MD rewrite."""
from __future__ import annotations
from . import domain, entities, keys, params

import asyncio
import logging
from urllib.parse import urlparse

import httpx



logger = logging.getLogger(__name__)


# Per-host versioned-docs base. RTD/Sphinx `/` → `/en/stable/` follow once
# + cache; failed probes cache the root so dead hosts aren't re-probed.
_version_base_cache: dict[str, str] = {}


async def _discover_version_base(
    scheme: str,
    host:   str,
    client: httpx.AsyncClient,
) -> str:
    """Discover & cache the canonical versioned docs base. URL ends with `/`."""
    if host in _version_base_cache:
        return _version_base_cache[host]
    root = f"{scheme}://{host}/"
    base = root
    try:
        r = await client.get(root, timeout = params.VERSION_PROBE_TIMEOUT_S, follow_redirects = True)
        base = domain.versioned_base(str(r.url))
    except Exception:  # noqa: BLE001 — a host that cannot be probed keeps its root as the base
        base = root
    _version_base_cache[host] = base
    return base


async def _sphinx_image_fallbacks(
    url:    str,
    client: httpx.AsyncClient,
) -> list[str]:
    """Sphinx `_images/` fallback URLs for 404'd images (see `domain.image_fallback_urls`); the versioned base is
    discovered once per host."""
    if not domain.wants_image_fallback(url):
        return []
    p = urlparse(url)
    return domain.image_fallback_urls(url, await _discover_version_base(p.scheme, p.netloc, client))


async def _fetch_remote_once(
    url:    str,
    client: httpx.AsyncClient,
) -> entities.Artifact | None:
    try:
        r = await client.get(url, timeout = params.TIMEOUT_S, follow_redirects = True)
    except Exception:  # noqa: BLE001 — an unreachable media file is skipped, the page keeps its original URL
        return None
    return domain.artifact_from_response(url, r.status_code, r.headers.get("content-type"), r.content)


async def _fetch_remote(
    url:    str,
    client: httpx.AsyncClient,
) -> entities.Artifact | None:
    """Remote fetch + Sphinx `_images/` fallback. Non-image and well-resolved
    refs pay nothing — fallback only fires on image-ish failure."""
    art = await _fetch_remote_once(url, client)
    if art is not None:
        return art
    for cand in await _sphinx_image_fallbacks(url, client):
        art = await _fetch_remote_once(cand, client)
        if art is not None:
            logger.info(f"[artifacts] _images fallback: {url} -> {cand}")
            return art
    return None


async def _save_one(
    payload: str,
    kind:    str,
    *,
    slug:    str,
    store,
    client:  httpx.AsyncClient,
) -> str | None:
    """Materialize one artifact → public path, or None on rejection/failure."""
    if kind == params.KIND_DATA:
        art = domain.parse_data_url(payload)
    else:
        art = await _fetch_remote(payload, client)
    if art is None:
        return None
    try:
        await store.add_artifact(
            slug         = slug,
            name         = art.name,
            data         = art.data,
            content_type = art.content_type,
        )
    except Exception as e:  # noqa: BLE001 — a failed write leaves the original URL in place
        logger.warning(
            f"[artifacts] put_object failed for "
            f"{(art.source_url or payload)[:80]}: {e}"
        )
        return None
    return keys.public_artifact_path(slug, art.name)


async def _resolve_payloads(
    payloads: dict[str, str],
    *,
    slug:    str,
    store,
    client:  httpx.AsyncClient,
) -> dict[str, str | None]:
    """Bounded-concurrency fan-out of `_save_one`. Same payload referenced
    100× → 1 download + 1 MinIO write (input is a unique map)."""
    if not payloads:
        return {}
    sem = asyncio.Semaphore(params.CONCURRENCY)

    async def _run(p: str, k: str) -> str | None:
        async with sem:
            return await _save_one(p, k, slug = slug, store = store, client = client)

    results = await asyncio.gather(
        *(_run(p, k) for p, k in payloads.items()),
        return_exceptions = False,
    )
    return dict(zip(payloads, results))


async def extract_and_save_artifacts(
    html:       str,
    source_url: str,
    *,
    slug:   str,
    store,
    client: httpx.AsyncClient,
) -> tuple[str, int]:
    """Materialize every media ref in `html` into MinIO; rewrite to
    `/api/v1/.../artifacts/{name}`. Per-artifact failures leave the original
    URL in place so the page stays viewable when a CDN flakes mid-ingest."""
    if not html:
        return html, 0
    soup = domain.parse_html(html)
    references = domain.html_references(soup, source_url)
    if not references:
        return html, 0

    unique_payloads: dict[str, str] = {}
    for ref in references:
        unique_payloads.setdefault(ref.payload, ref.kind)
    payload_to_public = await _resolve_payloads(
        unique_payloads, slug = slug, store = store, client = client,
    )
    n_saved = domain.rewrite_html_references(references, payload_to_public)
    if n_saved == 0:
        return html, 0
    return str(soup), n_saved


async def extract_and_save_artifacts_from_md(
    md:         str,
    source_url: str,
    *,
    slug:   str,
    store,
    client: httpx.AsyncClient,
) -> tuple[str, int]:
    """Markdown counterpart called from Store.add_page (every tier).
    For Tier 4 the HTML extractor runs first (richer: picture/srcset)."""
    if not md or not source_url:
        return md, 0
    payloads = domain.not_own_artifacts(domain.collect_md_payloads(md, source_url))
    if not payloads:
        return md, 0
    payload_to_public = await _resolve_payloads(
        payloads, slug = slug, store = store, client = client,
    )
    rep = domain.build_md_replacement_map(md, source_url, payload_to_public)
    if not rep:
        return md, 0
    return domain.apply_replacements(md, rep)
