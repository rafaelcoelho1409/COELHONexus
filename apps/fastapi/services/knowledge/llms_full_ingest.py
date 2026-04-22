"""
Knowledge Distiller — Tier 1 Ingestion (/llms-full.txt single-file fast path)

Dispatched by `services/knowledge/ingestion.py` when the resolver assigned
`tier == 1` — meaning Stage D's content-validated probe confirmed the site
publishes a real `/llms-full.txt` (markdown headings, ≥500 bytes, not a
SPA shell). This is the fastest ingestion strategy in the pipeline:

  1. One HTTP GET of `/llms-full.txt` at the host root
  2. `_write_raw()` the entire body to MinIO as ONE file

Typical wall time: 1-3 seconds, vs ~20 minutes for Tier 4 Playwright on
the same docs. A llms-full.txt is a publisher-curated, single-file,
LLM-ready dump of the entire documentation — exactly what we want.

URL STRATEGY:
  The resolver's D-probe fetches llms-full.txt at BOTH the docs_url and
  its host root, merging the best result per file. For Tier 1 assignment,
  at least one of those URLs returned VALID. We don't know which, so we
  try in order: host_root first (by far the most common convention per
  the llmstxt.org spec), then the deep docs_url path. Both-fail raises
  RuntimeError — dispatcher catches and falls back to Tier 4.

OUTPUT LAYOUT (same as other tiers):
  <study_root>/research/raw/<slug>-llms-full.md       — the file
  Slug format: `{host-slug}-llms-full` (e.g., `docs-langchain-com-llms-full`)

Reference: docs/KNOWLEDGE-DISTILLER-INGESTION-PIPELINE-PLAN.md §Step 3
llms-txt spec: https://llmstxt.org
"""
import logging
import re
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
    ManifestEntry,
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


async def ingest_llms_full_txt(
    cfg: DocsIngestionConfig,
    storage: MinIOStudyStorage) -> IngestResult:
    """
    Tier 1 ingestion. Called by the dispatcher when `cfg.tier == 1`.

    Tries host-root `/llms-full.txt` first (llmstxt.org convention), then
    falls back to the deep-path variant `docs_url/llms-full.txt`. Both-fail
    raises RuntimeError so the dispatcher falls back to Tier 4 Playwright.
    """
    parsed = urlparse(cfg.docs_url)
    host = (parsed.netloc or "").lower()
    if not host:
        raise RuntimeError(f"Tier 1: cannot parse host from docs_url={cfg.docs_url!r}")

    host_root = f"{parsed.scheme}://{parsed.netloc}"
    deep_base = cfg.docs_url.rstrip("/")
    # Candidate URLs in descending probability order
    candidates = [f"{host_root}/llms-full.txt"]
    if deep_base != host_root.rstrip("/"):
        candidates.append(f"{deep_base}/llms-full.txt")

    logger.info(
        f"[tier-1] start framework={cfg.framework!r} host={host} "
        f"candidates={candidates}"
    )

    progress = IngestProgress(cfg.study_id)
    await progress.start(tier = "llms_full_txt", total = 1)
    last_error: str | None = None
    try:
        async with httpx.AsyncClient(
            timeout = httpx.Timeout(_HTTP_TIMEOUT, connect = 10.0),
            follow_redirects = True,
        ) as client:
            for url in candidates:
                try:
                    resp = await _fetch(client, url)
                except Exception as e:
                    last_error = f"{type(e).__name__}: {e}"
                    logger.info(f"[tier-1] {url} failed: {last_error}")
                    continue
                if resp.status_code != 200:
                    last_error = f"HTTP {resp.status_code}"
                    logger.info(f"[tier-1] {url} → {last_error}")
                    continue
                body = resp.text
                if len(body) < _MIN_OK_BYTES:
                    last_error = f"body too short ({len(body)} bytes)"
                    logger.info(f"[tier-1] {url} → {last_error}")
                    continue
                # Success — write single file
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
                    last_error = "failed content-quality gate"
                    logger.warning(f"[tier-1] {url} → {last_error}")
                    continue
                await progress.update(current = 1, last_url = url)
                logger.info(
                    f"[tier-1] OK — 1 file, {entry.bytes} bytes "
                    f"(source={url})"
                )
                await progress.finish(status = "done")
                return IngestResult(
                    tier_used = "llms_full_txt",
                    total_files = 1,
                    total_bytes = entry.bytes,
                    manifest = [entry],
                    skipped_urls = [],
                )
        await progress.finish(status = "failed")
        raise RuntimeError(
            f"Tier 1 exhausted candidates for {cfg.framework!r}: "
            f"tried {candidates}, last error: {last_error}. "
            f"Dispatcher will fall back to Tier 4."
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
