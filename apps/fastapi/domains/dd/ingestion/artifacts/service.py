"""Artifact materialization: fetch (or decode) → MinIO write → HTML/MD rewrite."""
from __future__ import annotations

import asyncio
import logging
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup

from .domain import (
    ARTIFACT_ATTRS,
    build_md_replacement_map,
    classify_url,
    collect_md_payloads,
    ext_from_url,
    hash_name,
    is_imageish_url,
    parse_data_url,
    pick_largest_srcset,
)
from .entities import Artifact
from .keys import EXT_MIME, MIME_EXT, public_artifact_path
from .params import (
    CONCURRENCY,
    MAX_ARTIFACT_BYTES,
    MIN_ARTIFACT_BYTES,
    TIMEOUT_S,
)


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
        r = await client.get(root, timeout = 15.0, follow_redirects = True)
        fp = urlparse(str(r.url))
        path = fp.path or "/"
        if not path.endswith("/"):
            path = path.rsplit("/", 1)[0] + "/"
        base = f"{fp.scheme}://{fp.netloc}{path}"
    except Exception:
        base = root
    _version_base_cache[host] = base
    return base


async def _sphinx_image_fallbacks(
    url:    str,
    client: httpx.AsyncClient,
) -> list[str]:
    """Sphinx `_images/` fallback URLs for 404'd images. Tier-1 llms-full
    refs are page-relative; the real asset lives at
    `{versioned_docs_base}/_images/{basename}`."""
    if not is_imageish_url(url):
        return []
    p = urlparse(url)
    basename = (p.path or "").rsplit("/", 1)[-1]
    if not basename or "/_images/" in (p.path or ""):
        return []
    vbase = await _discover_version_base(p.scheme, p.netloc, client)
    cands: list[str] = []
    for c in (
        urljoin(vbase, f"_images/{basename}"),
        f"{p.scheme}://{p.netloc}/_images/{basename}",
    ):
        if c != url and c not in cands:
            cands.append(c)
    return cands


async def _fetch_remote_once(
    url:    str,
    client: httpx.AsyncClient,
) -> Artifact | None:
    try:
        r = await client.get(url, timeout = TIMEOUT_S, follow_redirects = True)
    except Exception:
        return None
    if r.status_code != 200:
        return None
    data = r.content
    if not (MIN_ARTIFACT_BYTES <= len(data) <= MAX_ARTIFACT_BYTES):
        return None
    ct = (r.headers.get("content-type") or "").split(";", 1)[0].strip().lower()
    # Refuse HTML/text — common 404-page-served-with-200 pattern.
    if ct.startswith(("text/html", "text/plain", "application/json")):
        # 404-page-served-as-200 pattern.
        return None
    ext = MIME_EXT.get(ct) or ext_from_url(url) or "bin"
    if not ct:
        ct = EXT_MIME.get(ext, "application/octet-stream")
    return Artifact(
        name         = hash_name(data, ext),
        data         = data,
        content_type = ct,
        source_url   = url,
    )


async def _fetch_remote(
    url:    str,
    client: httpx.AsyncClient,
) -> Artifact | None:
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
    if kind == "data":
        art = parse_data_url(payload)
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
    except Exception as e:
        logger.warning(
            f"[artifacts] put_object failed for "
            f"{(art.source_url or payload)[:80]}: {e}"
        )
        return None
    return public_artifact_path(slug, art.name)


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
    sem = asyncio.Semaphore(CONCURRENCY)

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
    try:
        soup = BeautifulSoup(html, "lxml")
    except Exception:
        soup = BeautifulSoup(html, "html.parser")

    # srcset gets normalized to its highest-density candidate.
    work: list[tuple] = []
    for tag_name, attr in ARTIFACT_ATTRS:
        for el in soup.find_all(tag_name):
            val = (el.get(attr) or "").strip()
            if not val:
                continue
            cls = classify_url(val, source_url)
            if cls is None:
                continue
            kind, payload = cls
            work.append((el, attr, val, kind, payload))
    for el in soup.find_all("source"):
        srcset = (el.get("srcset") or "").strip()
        if not srcset:
            continue
        full = pick_largest_srcset(srcset, source_url)
        if not full:
            continue
        if full.startswith("data:"):
            work.append((el, "srcset", srcset, "data", full))
        else:
            work.append((el, "srcset", srcset, "remote", full))

    if not work:
        return html, 0

    unique_payloads: dict[str, str] = {}
    for _, _, _, kind, payload in work:
        unique_payloads.setdefault(payload, kind)
    payload_to_public = await _resolve_payloads(
        unique_payloads, slug = slug, store = store, client = client,
    )

    n_saved = 0
    for el, attr, _orig, _kind, payload in work:
        public = payload_to_public.get(payload)
        if not public:
            continue
        if attr == "srcset":
            el["srcset"] = public
            # Also write sibling src so renderers that ignore srcset still pick up our copy.
            if not (el.get("src") or "").strip():
                el["src"] = public
        else:
            el[attr] = public
        n_saved += 1

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
    payloads = collect_md_payloads(md, source_url)
    # Skip URLs already at our artifact path — would re-probe for nothing.
    payloads = {
        p: k for p, k in payloads.items()
        if "/api/v1/docs-distiller/ingestion/" not in p
        or "/artifacts/" not in p
    }
    if not payloads:
        return md, 0
    payload_to_public = await _resolve_payloads(
        payloads, slug = slug, store = store, client = client,
    )
    rep = build_md_replacement_map(md, source_url, payload_to_public)
    if not rep:
        return md, 0
    # Sort by length DESC to avoid substring collisions (foo.com/img vs foo.com/img.png).
    n_rewrites = 0
    out = md
    for old in sorted(rep, key = len, reverse = True):
        n_rewrites += out.count(old)
        out = out.replace(old, rep[old])
    return out, n_rewrites
