"""
Knowledge Distiller — Tier 1 Ingestion (/llms-full.txt single-file fast path)

Dispatched by `services/knowledge/ingestion.py` when the resolver assigned
`tier == 1` — meaning sources.yaml has a curated `llms-full.txt` URL for
this framework. This is the fastest ingestion strategy in the pipeline:

  1. One HTTP GET of `cfg.docs_url` (the curated llms-full.txt URL)
  2. `_write_raw()` the entire body to MinIO as ONE file

Typical wall time: 1-3 seconds, vs ~20 minutes for Tier 4 Playwright on
the same docs. A llms-full.txt is a publisher-curated, single-file,
LLM-ready dump of the entire documentation — exactly what we want.

URL STRATEGY:
  The URL comes from sources.yaml (`llms_full_txt` section) and is used
  directly with no construction. Failure raises RuntimeError — dispatcher
  catches and falls back to Tier 4.

OUTPUT LAYOUT (same as other tiers):
  <study_root>/research/raw/<slug>-llms-full.md       — the file
  Slug format: `{host-slug}-llms-full` (e.g., `docs-langchain-com-llms-full`)

Reference: docs/KNOWLEDGE-DISTILLER-INGESTION-PIPELINE-PLAN.md §Step 3
llms-txt spec: https://llmstxt.org
"""
import logging
import re
import time
from urllib.parse import urlparse
import httpx
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential_jitter,
)

from schemas.knowledge.ingestion import (
    DocsIngestionConfig,
    IngestResult,
)
from services.knowledge.ingest_progress import IngestProgress
from services.knowledge.ingestion import _write_raw
from services.knowledge.storage import MinIOStudyStorage


logger = logging.getLogger(__name__)


# =============================================================================
# Constants
# =============================================================================
_USER_AGENT = "COELHONexus-KD-Tier1/1.0"
_HTTP_TIMEOUT = 30.0
# Min body size for a real llms-full.txt — aligns with the resolver's probe
# threshold so we don't accept a response the probe would've classified as
# SPA_FAKE. In practice real llms-full.txt files are 50KB-10MB.
_MIN_OK_BYTES = 500


# =============================================================================
# Public entry point
# =============================================================================
@retry(
    reraise = True,
    retry = retry_if_exception_type((httpx.TimeoutException, httpx.NetworkError)),
    stop = stop_after_attempt(3),
    wait = wait_exponential_jitter(initial = 1, max = 15),
)
async def _fetch(client: httpx.AsyncClient, url: str) -> httpx.Response:
    return await client.get(url, headers = {"User-Agent": _USER_AGENT})


# OP-50 (2026-04-25, post-Run-12) — manifest-shaped llms-full.txt detection.
# Run-12 evidence: Docker's https://docs.docker.com/llms-full.txt is actually
# a llms.txt-style manifest (URL: + Markdown: pointers, ZERO fenced code
# blocks across 318KB). Tier 1 ingested it as content; vault extraction
# yielded 0 hashes per chapter; every chapter sentinel'd. Detection: a real
# llms-full.txt has dozens-to-thousands of fenced code blocks (LangChain's
# was ~3000); a manifest has near-zero fences AND many URL lines.
class TierOneManifestDetected(Exception):
    """
    Raised by Tier 1 when the fetched llms-full.txt looks like a manifest
    (link index, no actual content). Caller (dispatcher) should fall
    through to Tier 2 (llms.txt parallel fetch) instead of Tier 4
    Playwright — the manifest's URL: + Markdown: pointers are exactly
    what Tier 2 consumes natively.
    """
    pass


_MANIFEST_MIN_URL_LINES = 100
_MANIFEST_MAX_FENCES = 5


def _looks_like_manifest(body: str) -> tuple[bool, dict]:
    """
    Heuristic check: is this body a manifest (link index) vs real content?
    A real llms-full.txt has many fenced code blocks (50+ typical, 1000+
    common). A manifest has near-zero fences AND many URL: / Markdown: lines.
    Returns (is_manifest, stats_dict).
    """
    fence_count = len(re.findall(r"(?m)^```", body))
    url_count = len(re.findall(r"(?m)^URL:\s+https?://", body))
    md_pointer_count = len(re.findall(
        r"(?m)^Markdown:\s+https?://\S+\.md\s*$", body
    ))
    is_manifest = (
        fence_count < _MANIFEST_MAX_FENCES
        and (url_count > _MANIFEST_MIN_URL_LINES
             or md_pointer_count > _MANIFEST_MIN_URL_LINES)
    )
    return is_manifest, {
        "fence_count": fence_count,
        "url_lines": url_count,
        "md_pointers": md_pointer_count,
    }


async def ingest_llms_full_txt(
    cfg: DocsIngestionConfig,
    storage: MinIOStudyStorage) -> IngestResult:
    """
    Tier 1 ingestion. Called by the dispatcher when `cfg.tier == 1`.

    Fetches `cfg.docs_url` directly — the resolver supplies the exact
    llms-full.txt URL from sources.yaml; no construction. Failure raises
    RuntimeError so the dispatcher falls back to Tier 4 Playwright.

    OP-50 (2026-04-25): on a successful fetch, checks if the body is a
    manifest (URL/Markdown pointers, no fences). If so, raises
    `TierOneManifestDetected` so the dispatcher can fall through to Tier 2
    instead of treating the manifest as content.
    """
    url = cfg.docs_url
    parsed = urlparse(url)
    host = (parsed.netloc or "").lower()
    if not host:
        raise RuntimeError(f"Tier 1: cannot parse host from docs_url={url!r}")

    logger.info(f"[tier-1] start framework={cfg.framework!r} url={url}")

    progress = IngestProgress(cfg.study_id)
    await progress.start(tier = "llms_full_txt", total = 1)
    try:
        async with httpx.AsyncClient(
            timeout = httpx.Timeout(_HTTP_TIMEOUT, connect = 10.0),
            follow_redirects = True,
        ) as client:
            t0 = time.monotonic()
            try:
                resp = await _fetch(client, url)
            except Exception as e:
                err = f"{type(e).__name__}: {e}"
                await progress.record_url(
                    url, status="fetch_error", tier="llms_full_txt",
                    fetch_ms=int((time.monotonic() - t0) * 1000),
                    error_msg=err,
                )
                await progress.finish(status = "failed")
                raise RuntimeError(
                    f"Tier 1 fetch failed for {cfg.framework!r} at {url}: {err}"
                )
            fetch_ms = int((time.monotonic() - t0) * 1000)
            if resp.status_code != 200:
                await progress.record_url(
                    url, status="http_error", tier="llms_full_txt",
                    http_code=resp.status_code, fetch_ms=fetch_ms,
                    bytes_fetched=len(resp.text or ""),
                    error_msg=f"HTTP {resp.status_code}",
                )
                await progress.finish(status = "failed")
                raise RuntimeError(
                    f"Tier 1: {url} → HTTP {resp.status_code}"
                )
            body = resp.text
            if len(body) < _MIN_OK_BYTES:
                await progress.record_url(
                    url, status="extract_empty", tier="llms_full_txt",
                    http_code=resp.status_code, fetch_ms=fetch_ms,
                    bytes_fetched=len(body), extracted_chars=0,
                    error_msg=f"body too short ({len(body)} bytes)",
                )
                await progress.finish(status = "failed")
                raise RuntimeError(
                    f"Tier 1: {url} body too short ({len(body)} bytes < "
                    f"{_MIN_OK_BYTES})"
                )
            # OP-50 (2026-04-25) — manifest detection. If the file is
            # actually a llms.txt-style link index disguised as
            # llms-full.txt, raise the dispatcher signal so we fall
            # through to Tier 2 instead of writing useless content.
            is_manifest, m_stats = _looks_like_manifest(body)
            if is_manifest:
                logger.warning(
                    f"[tier-1] {url} looks like a MANIFEST "
                    f"(fences={m_stats['fence_count']}, "
                    f"url_lines={m_stats['url_lines']}, "
                    f"md_pointers={m_stats['md_pointers']}) — "
                    f"raising TierOneManifestDetected so dispatcher "
                    f"falls through to Tier 2"
                )
                await progress.record_url(
                    url, status="downgrade", tier="llms_full_txt",
                    http_code=resp.status_code, fetch_ms=fetch_ms,
                    bytes_fetched=len(body), extracted_chars=0,
                    error_msg="manifest detected; falling through to tier 2",
                )
                await progress.finish(status = "downgrade")
                raise TierOneManifestDetected(f"{url}: {m_stats}")

            slug = _derive_slug(host)
            entry = await _write_raw(
                storage = storage,
                study_root = cfg.study_root,
                slug = slug,
                content = body,
                url = url,
                tier = "llms_full_txt",
                cfg = cfg,
            )
            if entry is None:
                await progress.record_url(
                    url, status="extract_empty", tier="llms_full_txt",
                    http_code=resp.status_code, fetch_ms=fetch_ms,
                    bytes_fetched=len(body), extracted_chars=0,
                    error_msg="failed content-quality gate",
                )
                await progress.finish(status = "failed")
                raise RuntimeError(
                    f"Tier 1: {url} failed content-quality gate"
                )
            await progress.update(current = 1, last_url = url)
            await progress.record_url(
                url, status="success", tier="llms_full_txt",
                http_code=resp.status_code, fetch_ms=fetch_ms,
                bytes_fetched=len(body), extracted_chars=len(body),
            )
            logger.info(
                f"[tier-1] OK — 1 file, {entry.bytes} bytes (source={url})"
            )
            await progress.finish(status = "done")
            return IngestResult(
                tier_used = "llms_full_txt",
                total_files = 1,
                total_bytes = entry.bytes,
                manifest = [entry],
                skipped_urls = [],
            )
    finally:
        await progress.close()


def _derive_slug(host: str) -> str:
    """
    `docs.langchain.com` → `docs-langchain-com-llms-full`
    Stable, filesystem-safe.
    """
    slug = re.sub(r"[^a-z0-9]+", "-", host.lower()).strip("-")
    return f"{slug}-llms-full"[:120] or "llms-full"
