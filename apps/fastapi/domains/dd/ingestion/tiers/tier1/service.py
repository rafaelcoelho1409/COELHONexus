"""Tier 1 — fetch a single `llms-full.txt` bundle.

Fastest ingestion path: one HTTP GET of a publisher-curated, LLM-ready dump
of an entire docs site (typically 50 KB – 10 MB of clean markdown). The
post-process step splits it on H1/H2 boundaries into per-section pages.

Falls through to Tier 2 (raises `ManifestDetected`) when the fetched body
is actually a llms.txt-style link index disguised as llms-full.txt
(detected by absence of fenced code blocks + many URL: pointers — a real
content bundle has dozens-to-thousands of fences).
"""
import logging
import time
from urllib.parse import urlparse

import httpx
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential_jitter,
)

from ...progress import Progress
from ...storage import Store
from ..errors import ManifestDetected
from .domain import host_slug, looks_like_manifest
from .params import MIN_OK_BYTES, TIMEOUT_S, USER_AGENT


logger = logging.getLogger(__name__)


@retry(
    reraise = True,
    retry = retry_if_exception_type((httpx.TimeoutException, httpx.NetworkError)),
    stop = stop_after_attempt(3),
    wait = wait_exponential_jitter(initial = 1, max = 15),
)
async def _fetch(client: httpx.AsyncClient, url: str) -> httpx.Response:
    return await client.get(url, headers = {"User-Agent": USER_AGENT})


async def run(
    *,
    url: str,
    framework_slug: str,
    progress: Progress,
    store: Store,
) -> int:
    """Fetch the bundle and write it as one entry. Returns 1 on success.

    Raises:
        ManifestDetected — body is a manifest; dispatcher should try Tier 2
        RuntimeError      — fetch failed, body too short, or HTTP non-200
    """
    host = (urlparse(url).netloc or "").lower()
    if not host:
        raise RuntimeError(f"Tier 1: cannot parse host from url={url!r}")
    logger.info(f"[tier-1] framework={framework_slug} url={url}")
    await progress.start(tier = "llms_full", total = 1)
    await progress.raise_if_cancelled()
    async with httpx.AsyncClient(
        timeout = httpx.Timeout(TIMEOUT_S, connect = 10.0),
        follow_redirects = True,
    ) as client:
        t0 = time.monotonic()
        try:
            resp = await _fetch(client, url)
        except Exception as e:
            err = f"{type(e).__name__}: {e}"
            await progress.record_url(
                url,
                status = "fetch_error",
                tier = "llms_full",
                fetch_ms = int((time.monotonic() - t0) * 1000),
                error_msg = err,
            )
            await progress.finish(status = "failed")
            raise RuntimeError(f"Tier 1 fetch failed for {url}: {err}")
        fetch_ms = int((time.monotonic() - t0) * 1000)
        body = resp.text or ""
        if resp.status_code != 200:
            await progress.record_url(
                url,
                status = "http_error",
                tier = "llms_full",
                http_code = resp.status_code,
                fetch_ms = fetch_ms,
                bytes_fetched = len(body),
                error_msg = f"HTTP {resp.status_code}",
            )
            await progress.finish(status = "failed")
            raise RuntimeError(f"Tier 1: {url} → HTTP {resp.status_code}")
        if len(body) < MIN_OK_BYTES:
            await progress.record_url(
                url,
                status = "extract_empty",
                tier = "llms_full",
                http_code = resp.status_code,
                fetch_ms = fetch_ms,
                bytes_fetched = len(body),
                extracted_chars = 0,
                error_msg = f"body too short ({len(body)}B)",
            )
            await progress.finish(status = "failed")
            raise RuntimeError(f"Tier 1: {url} body too short ({len(body)}B)")

        is_manifest, stats = looks_like_manifest(body)
        if is_manifest:
            logger.warning(
                f"[tier-1] {url} looks like a manifest "
                f"(fences={stats['fences']}, urls={stats['urls']}, "
                f"md_pointers={stats['md_pointers']}) — falling to Tier 2"
            )
            await progress.record_url(
                url,
                status = "downgrade",
                tier = "llms_full",
                http_code = resp.status_code,
                fetch_ms = fetch_ms,
                bytes_fetched = len(body),
                error_msg = "manifest detected; falling to tier 2",
            )
            await progress.finish(status = "downgrade")
            raise ManifestDetected(f"{url}: {stats}")
        slug = host_slug(host)
        await store.add_page(
            slug = slug,
            url = url,
            body = body,
            tier = "llms_full",
            title = slug,
        )
        await progress.record_url(
            url,
            status = "success",
            tier = "llms_full",
            http_code = resp.status_code,
            fetch_ms = fetch_ms,
            bytes_fetched = len(body),
            extracted_chars = len(body),
        )
        await progress.update(current = 1, last_url = url)
        await progress.finish(status = "done")
        return 1
