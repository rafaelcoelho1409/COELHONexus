"""Per-page artifact extraction — download every image / video / audio
referenced by a fetched HTML page and rewrite the URLs to point at our
own MinIO copies served via ``/api/v1/docs-distiller/ingestion/{slug}/artifacts/{name}``.

Why this matters
~~~~~~~~~~~~~~~~
The Tier 4 pipeline converts HTML → markdown via ``markdownify``, which
preserves ``<img src="…">`` references verbatim. Without intervention,
the saved markdown carries upstream URLs:

  ![alt](https://adtk.readthedocs.io/_static/arundo_logo_black.png)
  ![](data:image/png;base64,iVBORw0KGgo… 2 MB of payload …)

Two failure modes that creates:

1. **Upstream churn** — links rot when projects rename `_static` dirs,
   move to a new RTD subdomain, or revoke their CDN. Our corpus then
   shows broken images in the drawer + chapter views forever.
2. **Bloat from inline base64** — UMAP's `basic_usage.html` ships **4.2 MB**
   of inline notebook-output PNGs as ``data:`` URLs; SHAP-IQ's
   `plot_proxyspex.html` is **2.3 MB**. Storing these as a single fat
   markdown file blows out the digest stage (token budget) and
   the rendered drawer (browser parses 4 MB of base64 to display two charts).

This module solves both: every artifact reference is downloaded /
decoded once at ingest time, persisted at
``ingestion/{slug}/artifacts/{sha256[:16]}.{ext}`` (content-addressed
+ deduped), and the HTML's URL is rewritten to a stable served path
**before** ``html_to_markdown`` runs so the saved markdown carries our
references — not the upstream ones — from the moment it lands in MinIO.

Scope
~~~~~
Captures ``<img src>``, ``<img data-src>`` (lazy-load pattern),
``<video src>``, ``<video poster>``, ``<audio src>``, ``<source src>``
across image/audio/video MIME families. ``<picture><source srcset>`` is
expanded into its largest candidate. Conservative size cap
(``_MAX_ARTIFACT_BYTES = 25 MB``) drops over-budget assets rather than
risking memory pressure on the single-node K3s. HTML/text returned by
upstream redirects is rejected (e.g. CDN 404 pages masquerading as
images).

Operational
~~~~~~~~~~~
Concurrency is bounded (``_CONCURRENCY = 8``) and shares the same
``httpx.AsyncClient`` as Tier 4's fetcher so connection pooling and
DNS cache stay warm. Per-URL dedup means the publisher's repeated
references to a single asset across pages cost one download + one
MinIO write — not N.
"""
import asyncio
import base64
import hashlib
import logging
import re
from typing import NamedTuple
from urllib.parse import urljoin, urlparse

import httpx
from bs4 import BeautifulSoup


logger = logging.getLogger(__name__)


_MAX_ARTIFACT_BYTES = 25 * 1024 * 1024  # 25 MB cap per artifact
_MIN_ARTIFACT_BYTES = 32                # tiny payloads ≈ tracking pixels
_TIMEOUT_S = 30.0
_CONCURRENCY = 8


# Canonical MIME → file extension. The ext determines the served
# filename only; the actual content-type at serve time comes from
# whatever the upload-time put_object recorded.
_MIME_EXT: dict[str, str] = {
    "image/png": "png", "image/jpeg": "jpg", "image/jpg": "jpg",
    "image/gif": "gif", "image/svg+xml": "svg", "image/webp": "webp",
    "image/avif": "avif", "image/x-icon": "ico", "image/vnd.microsoft.icon": "ico",
    "image/bmp": "bmp", "image/tiff": "tiff",
    "video/mp4": "mp4", "video/webm": "webm", "video/quicktime": "mov",
    "video/x-matroska": "mkv", "video/ogg": "ogv",
    "audio/mpeg": "mp3", "audio/ogg": "ogg", "audio/wav": "wav",
    "audio/x-wav": "wav", "audio/mp4": "m4a", "audio/aac": "aac",
    "audio/flac": "flac", "audio/webm": "weba",
}

_EXT_MIME: dict[str, str] = {
    "png": "image/png", "jpg": "image/jpeg", "jpeg": "image/jpeg",
    "gif": "image/gif", "svg": "image/svg+xml", "webp": "image/webp",
    "avif": "image/avif", "ico": "image/x-icon", "bmp": "image/bmp",
    "tiff": "image/tiff",
    "mp4": "video/mp4", "webm": "video/webm", "mov": "video/quicktime",
    "mkv": "video/x-matroska", "ogv": "video/ogg",
    "mp3": "audio/mpeg", "ogg": "audio/ogg", "wav": "audio/wav",
    "m4a": "audio/mp4", "aac": "audio/aac", "flac": "audio/flac",
    "weba": "audio/webm",
}

# Tags + attribute pairs we extract URLs from. ``data-src`` covers
# lazy-load patterns (most modern docs themes set both src and data-src).
# ``poster`` is the video preview image. ``srcset`` is handled
# separately (it can carry multiple candidates).
_ARTIFACT_ATTRS: tuple[tuple[str, str], ...] = (
    ("img", "src"), ("img", "data-src"),
    ("video", "src"), ("video", "poster"),
    ("audio", "src"),
    ("source", "src"),
)

_DATA_URL_RE = re.compile(
    r"^data:(?P<mime>[\w/\-+.]+)"
    r"(?:;[\w-]+=[\w-]+)*"        # arbitrary `;k=v` params (charset, etc.)
    r"(?:;(?P<enc>base64))?,"
    r"(?P<data>.*)$",
    re.DOTALL,
)


class Artifact(NamedTuple):
    name: str          # ``{sha256[:16]}.{ext}`` — content-addressed
    data: bytes
    content_type: str
    source_url: str    # for logs; "data:" for inline payloads


def _hash_name(data: bytes, ext: str) -> str:
    return hashlib.sha256(data).hexdigest()[:16] + "." + (ext or "bin")


def _ext_from_url(url: str) -> str:
    path = (urlparse(url).path or "").rsplit(".", 1)
    if len(path) != 2:
        return ""
    ext = path[1].lower().split("?", 1)[0].split("#", 1)[0]
    return ext if ext in _EXT_MIME else ""


def _parse_data_url(src: str) -> Artifact | None:
    m = _DATA_URL_RE.match(src)
    if not m:
        return None
    mime = (m.group("mime") or "").lower()
    enc = m.group("enc")
    data_str = m.group("data") or ""
    try:
        if enc == "base64":
            # Tolerate stray whitespace some encoders sprinkle in.
            data = base64.b64decode(re.sub(r"\s+", "", data_str), validate=False)
        else:
            data = data_str.encode("utf-8")  # url-encoded — rare for media
    except Exception:
        return None
    if not (_MIN_ARTIFACT_BYTES <= len(data) <= _MAX_ARTIFACT_BYTES):
        return None
    ext = _MIME_EXT.get(mime, "bin")
    return Artifact(
        name=_hash_name(data, ext), data=data,
        content_type=mime or "application/octet-stream",
        source_url="data:",
    )


# Image extensions eligible for the Sphinx ``_images/`` fallback probe.
_IMAGE_EXTS = frozenset({
    "png", "jpg", "jpeg", "gif", "svg", "webp", "avif", "bmp", "tiff", "ico",
})

# Per-host cache of the canonical *versioned* docs base, discovered by
# following the host-root redirect ONCE (RTD/Sphinx sites 302 ``/`` ->
# ``/en/stable/``). Module-level so it survives across pages within a
# worker — version bases don't move inside a deploy. host -> base URL
# (always ends with ``/``); a failed probe caches the bare host root so a
# dead host isn't re-probed on every image.
_version_base_cache: dict[str, str] = {}


def _is_imageish_url(url: str) -> bool:
    parts = (urlparse(url).path or "").rsplit(".", 1)
    if len(parts) != 2:
        return False
    return parts[1].split("?", 1)[0].split("#", 1)[0].lower() in _IMAGE_EXTS


async def _discover_version_base(
    scheme: str, host: str, client: httpx.AsyncClient,
) -> str:
    """Follow the host-root redirect once to learn the canonical versioned
    docs base (RTD/Sphinx 302 ``/`` -> ``/en/stable/``). Cached per host.
    Returns a URL ending in ``/``."""
    if host in _version_base_cache:
        return _version_base_cache[host]
    root = f"{scheme}://{host}/"
    base = root
    try:
        r = await client.get(root, timeout=15.0, follow_redirects=True)
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
    url: str, client: httpx.AsyncClient,
) -> list[str]:
    """Derive Sphinx ``_images/`` candidates for an image URL that failed
    to fetch. Tier-1 llms-full bundles routinely carry page-relative image
    refs (``images/foo.svg``) that resolve to a 404 against the bundle URL
    — the real asset lives at ``{versioned_docs_base}/_images/{basename}``
    (Sphinx's flat image dir). Returns absolute candidates in priority
    order; empty when ``url`` isn't an image or is already an ``_images``
    path that genuinely 404'd."""
    if not _is_imageish_url(url):
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
    url: str, client: httpx.AsyncClient,
) -> Artifact | None:
    try:
        r = await client.get(url, timeout=_TIMEOUT_S, follow_redirects=True)
    except Exception:
        return None
    if r.status_code != 200:
        return None
    data = r.content
    if not (_MIN_ARTIFACT_BYTES <= len(data) <= _MAX_ARTIFACT_BYTES):
        return None
    ct = (r.headers.get("content-type") or "").split(";", 1)[0].strip().lower()
    # Refuse HTML/text masquerading as media — common 404-page-with-200 pattern.
    if ct.startswith(("text/html", "text/plain", "application/json")):
        return None
    ext = _MIME_EXT.get(ct) or _ext_from_url(url) or "bin"
    if not ct:
        ct = _EXT_MIME.get(ext, "application/octet-stream")
    return Artifact(name=_hash_name(data, ext), data=data,
                    content_type=ct, source_url=url)


async def _fetch_remote(url: str, client: httpx.AsyncClient) -> Artifact | None:
    """Fetch a remote artifact, with a Sphinx ``_images/`` fallback for
    page-relative image refs that 404 on naive resolution (Tier 1/2/3
    markdown bundles). The fallback only fires on failure of an image-ish
    URL, so non-image and well-resolved refs pay nothing extra."""
    art = await _fetch_remote_once(url, client)
    if art is not None:
        return art
    for cand in await _sphinx_image_fallbacks(url, client):
        art = await _fetch_remote_once(cand, client)
        if art is not None:
            logger.info(f"[artifacts] _images fallback: {url} -> {cand}")
            return art
    return None


def _pick_largest_srcset(srcset: str, base_url: str) -> str | None:
    """Resolve ``<source srcset="a.png 1x, b.png 2x, c.png 3x">`` to its
    highest-resolution candidate (last w/x descriptor wins). Returns
    an absolute URL or ``None`` if the srcset is malformed."""
    candidates: list[tuple[float, str]] = []
    for piece in srcset.split(","):
        piece = piece.strip()
        if not piece:
            continue
        parts = piece.split(None, 1)
        url = parts[0].strip()
        if not url:
            continue
        density = 1.0
        if len(parts) > 1:
            desc = parts[1].strip().lower()
            if desc.endswith("x"):
                try: density = float(desc[:-1])
                except Exception: density = 1.0
            elif desc.endswith("w"):
                try: density = float(desc[:-1]) / 1000.0
                except Exception: density = 1.0
        full = urljoin(base_url, url)
        if full.startswith(("http://", "https://", "data:")):
            candidates.append((density, full))
    if not candidates:
        return None
    candidates.sort(key=lambda x: -x[0])
    return candidates[0][1]


def _public_artifact_path(slug: str, name: str) -> str:
    return f"/api/v1/docs-distiller/ingestion/{slug}/artifacts/{name}"


def _classify_url(url: str, base: str) -> tuple[str, str] | None:
    """Map a raw attribute value (data URL, absolute http(s), //, or
    relative) into ``(kind, resolved_payload)`` where ``kind ∈ {'data',
    'remote'}``. Returns ``None`` for unsupported schemes (mailto:,
    javascript:, tel:, anchors, in-page #fragments, etc.)."""
    u = (url or "").strip()
    if not u or u.startswith(("#", "javascript:", "mailto:", "tel:")):
        return None
    if u.startswith("data:"):
        return ("data", u)
    if u.startswith("//"):
        return ("remote", "https:" + u)
    if u.startswith(("http://", "https://")):
        return ("remote", u)
    # Relative — resolve against the page URL.
    full = urljoin(base, u)
    if full.startswith(("http://", "https://")):
        return ("remote", full)
    return None


async def _save_one(
    payload: str, kind: str, *, slug: str, store, client: httpx.AsyncClient,
) -> str | None:
    """Materialize a single artifact into MinIO + return its public path,
    or ``None`` when the payload is too small, too large, the wrong
    content-type, or the put_object failed. Shared by both the HTML and
    markdown extractors below."""
    if kind == "data":
        art = _parse_data_url(payload)
    else:
        art = await _fetch_remote(payload, client)
    if art is None:
        return None
    try:
        await store.add_artifact(
            slug=slug, name=art.name, data=art.data,
            content_type=art.content_type,
        )
    except Exception as e:
        logger.warning(
            f"[artifacts] put_object failed for "
            f"{(art.source_url or payload)[:80]}: {e}"
        )
        return None
    return _public_artifact_path(slug, art.name)


async def _resolve_payloads(
    payloads: dict[str, str], *, slug: str, store, client: httpx.AsyncClient,
) -> dict[str, str | None]:
    """Run ``_save_one`` over a unique-payload map with bounded
    concurrency. Returns ``{payload: public_path_or_None}``. Same payload
    referenced 100x → 1 download + 1 MinIO write."""
    if not payloads:
        return {}
    sem = asyncio.Semaphore(_CONCURRENCY)

    async def _run(p: str, k: str) -> str | None:
        async with sem:
            return await _save_one(p, k, slug=slug, store=store, client=client)

    results = await asyncio.gather(
        *(_run(p, k) for p, k in payloads.items()),
        return_exceptions=False,
    )
    return dict(zip(payloads, results))


async def extract_and_save_artifacts(
    html: str, source_url: str, *, slug: str, store, client: httpx.AsyncClient,
) -> tuple[str, int]:
    """Parse ``html``, materialize every media reference into MinIO,
    rewrite the HTML to use ``/api/v1/.../artifacts/{name}`` paths, and
    return ``(rewritten_html, n_saved)``.

    Safe to call on every fetched page — no-op when zero media tags are
    present. Errors on individual artifacts (network, MinIO, decode) drop
    that asset silently and leave its original URL in place, so the page
    stays viewable even if a CDN flakes mid-ingest.
    """
    if not html:
        return html, 0
    try:
        soup = BeautifulSoup(html, "lxml")
    except Exception:
        soup = BeautifulSoup(html, "html.parser")

    # 1. Gather work items as (element, attr, current_attr_value, kind, payload).
    #    srcset is normalized to its highest-density candidate.
    work: list[tuple] = []
    for tag_name, attr in _ARTIFACT_ATTRS:
        for el in soup.find_all(tag_name):
            val = (el.get(attr) or "").strip()
            if not val:
                continue
            cls = _classify_url(val, source_url)
            if cls is None:
                continue
            kind, payload = cls
            work.append((el, attr, val, kind, payload))
    for el in soup.find_all("source"):
        srcset = (el.get("srcset") or "").strip()
        if not srcset:
            continue
        full = _pick_largest_srcset(srcset, source_url)
        if not full:
            continue
        if full.startswith("data:"):
            work.append((el, "srcset", srcset, "data", full))
        else:
            work.append((el, "srcset", srcset, "remote", full))

    if not work:
        return html, 0

    # 2. Resolve every unique payload once (shared dedup with the
    #    markdown extractor's worker — `_save_one`).
    unique_payloads: dict[str, str] = {}
    for _, _, _, kind, payload in work:
        unique_payloads.setdefault(payload, kind)
    payload_to_public = await _resolve_payloads(
        unique_payloads, slug=slug, store=store, client=client,
    )

    # 3. Rewrite element attributes for every successfully-saved artifact.
    n_saved = 0
    for el, attr, _orig, _kind, payload in work:
        public = payload_to_public.get(payload)
        if not public:
            continue
        if attr == "srcset":
            # Collapse srcset to a single URL — the rewritten asset is one file.
            el["srcset"] = public
            # Also overwrite a sibling src if the same element has one, so
            # legacy renderers that ignore srcset still pick up our copy.
            if not (el.get("src") or "").strip():
                el["src"] = public
        else:
            el[attr] = public
        n_saved += 1

    if n_saved == 0:
        return html, 0
    return str(soup), n_saved


# =====================================================================
# Markdown variant — Tier 1 (llms-full.txt), Tier 2 (llms.txt) and any
# other path that lands as MARKDOWN rather than HTML. Covers the Alibi
# Explain / dbt / FastMCP / LiteLLM / Streamlit / dagster / TRL /
# Optimum / Roboflow / Inference / SGLang / Projectdiscovery /
# Browser-Use / Dask / Pydantic / Terragrunt / Evidently sources whose
# llms-full.txt ships pre-baked markdown with raw upstream image URLs.
# =====================================================================

# ![alt](url) — alt may contain anything except ], URL stops at first ) or
# whitespace, optional " title " ignored. Doesn't try to match nested
# brackets or escaped parens (vanishingly rare in published docs).
_MD_IMG_RE = re.compile(
    r'!\[(?P<alt>[^\]]*)\]'
    r'\((?P<url>[^)\s]+)'
    r'(?:\s+"[^"]*")?'
    r'\)',
)

# HTML image/media tags embedded inside markdown (CommonMark allows raw
# HTML). We match the OPENING tag only and pull src/data-src/poster/srcset
# attributes from it — this is a forgiving fast-path, not a full HTML
# parser. For Tier 1 markdown, that's enough; for HTML pages we already
# use BeautifulSoup in ``extract_and_save_artifacts``.
_MD_HTML_TAG_RE = re.compile(
    r'<(?P<tag>img|video|audio|source)\b(?P<attrs>[^>]*)>',
    re.IGNORECASE,
)
_HTML_ATTR_RE = re.compile(
    r"""(?P<attr>\b(?:src|data-src|poster|srcset))\s*=\s*"""
    r"""(?P<q>['"])(?P<value>.*?)(?P=q)""",
    re.IGNORECASE | re.DOTALL,
)


def _collect_md_payloads(md: str, source_url: str) -> dict[str, str]:
    """Scan ``md`` for image-bearing references and return a
    ``{payload: kind}`` dict ready for ``_resolve_payloads``."""
    payloads: dict[str, str] = {}
    # Pass 1: ![alt](url)
    for m in _MD_IMG_RE.finditer(md):
        cls = _classify_url(m.group("url"), source_url)
        if cls is not None:
            payloads.setdefault(cls[1], cls[0])
    # Pass 2: raw <img>/<video>/<audio>/<source> tags
    for tagm in _MD_HTML_TAG_RE.finditer(md):
        attrs = tagm.group("attrs") or ""
        for am in _HTML_ATTR_RE.finditer(attrs):
            attr = am.group("attr").lower()
            value = am.group("value").strip()
            if not value:
                continue
            if attr == "srcset":
                picked = _pick_largest_srcset(value, source_url)
                if not picked:
                    continue
                cls = ("data", picked) if picked.startswith("data:") \
                      else _classify_url(picked, source_url)
            else:
                cls = _classify_url(value, source_url)
            if cls is not None:
                payloads.setdefault(cls[1], cls[0])
    return payloads


def _build_md_replacement_map(
    md: str, source_url: str, payload_to_public: dict[str, str | None],
) -> dict[str, str]:
    """Build a ``{original_url_text: public_path}`` map for substring
    replacement in the markdown body. Keyed on the EXACT URL substring as
    it appears in the source so we can do safe ``str.replace`` (the URL
    is unique enough — and our public paths can't accidentally collide
    with anything else in the doc)."""
    rep: dict[str, str] = {}
    # ![alt](url) — key by the raw `url` string
    for m in _MD_IMG_RE.finditer(md):
        raw = m.group("url")
        cls = _classify_url(raw, source_url)
        if cls is None:
            continue
        public = payload_to_public.get(cls[1])
        if public:
            rep[raw] = public
    # <tag ... src="..."/data-src/poster/srcset>
    for tagm in _MD_HTML_TAG_RE.finditer(md):
        attrs = tagm.group("attrs") or ""
        for am in _HTML_ATTR_RE.finditer(attrs):
            attr = am.group("attr").lower()
            raw = am.group("value").strip()
            if not raw:
                continue
            if attr == "srcset":
                # srcset is multi-URL; we collapse to the single picked
                # candidate so callers don't have to parse it again.
                picked = _pick_largest_srcset(raw, source_url)
                cls = ("data", picked) if (picked and picked.startswith("data:")) \
                      else (_classify_url(picked, source_url) if picked else None)
                public = payload_to_public.get(cls[1]) if cls else None
                if public:
                    rep[raw] = public  # full original srcset → single URL
            else:
                cls = _classify_url(raw, source_url)
                public = payload_to_public.get(cls[1]) if cls else None
                if public:
                    rep[raw] = public
    return rep


async def extract_and_save_artifacts_from_md(
    md: str, source_url: str, *, slug: str, store, client: httpx.AsyncClient,
) -> tuple[str, int]:
    """Markdown counterpart of ``extract_and_save_artifacts``. Walks
    ``![alt](url)`` references AND raw HTML ``<img>/<video>/<audio>/<source>``
    tags embedded in the markdown body, downloads each, and rewrites the
    URL to ``/api/v1/.../artifacts/{name}``. Returns ``(rewritten_md,
    n_saved)``.

    Called from ``Store.add_page`` as the universal artifact hook so every
    tier (1 = llms-full.txt, 2 = llms.txt, 3 = sitemap, 4 = HTML crawl,
    5 = GitHub) flows through one save path. For Tier 4 the HTML
    extractor runs FIRST against the raw HTML (richer — picks up
    ``picture/srcset``, scoped at the article root, etc.); whatever
    artifact URLs survive into the converted markdown either ARE ALREADY
    rewritten (no-op for this pass) or were missed (this pass catches them).
    """
    if not md or not source_url:
        return md, 0
    payloads = _collect_md_payloads(md, source_url)
    # Skip URLs that have already been rewritten to OUR artifact path —
    # they're a no-op and the network probe is wasted.
    payloads = {
        p: k for p, k in payloads.items()
        if "/api/v1/docs-distiller/ingestion/" not in p
        or "/artifacts/" not in p
    }
    if not payloads:
        return md, 0
    payload_to_public = await _resolve_payloads(
        payloads, slug=slug, store=store, client=client,
    )
    rep = _build_md_replacement_map(md, source_url, payload_to_public)
    if not rep:
        return md, 0
    # Apply replacements. URLs are unique enough that plain str.replace
    # is safe — and incidentally rewrites the same URL in any link
    # context too, which is harmless (a click serves our cached copy).
    # Sort by length DESC to make sure a shorter URL doesn't replace
    # inside a longer URL's body (e.g. ``foo.com/img`` vs ``foo.com/img.png``).
    # Count rewrites (matching the HTML extractor's semantic: every
    # reference rewritten, NOT unique blobs saved).
    n_rewrites = 0
    out = md
    for old in sorted(rep, key=len, reverse=True):
        n_rewrites += out.count(old)
        out = out.replace(old, rep[old])
    return out, n_rewrites
