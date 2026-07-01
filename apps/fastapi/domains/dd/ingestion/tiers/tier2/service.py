"""Fetch llms.txt index (AnswerDotAI spec), then all linked pages concurrently. Markdown responses pass through; HTML goes through the extractor. Progress becomes determinate after index parse."""
import asyncio
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

from ...artifacts import extract_and_save_artifacts
from ...progress import Progress
from ...storage import Store
from ..errors import EmptyLinksDetected
from ..extract import extract_title, html_to_markdown
from .domain import is_markdown_response, parse_index, slugify
from .params import CONCURRENCY, MIN_OK_BYTES, TIMEOUT_S, USER_AGENT


logger = logging.getLogger(__name__)


@retry(
    reraise = True,
    retry = retry_if_exception_type((httpx.TimeoutException, httpx.NetworkError)),
    stop = stop_after_attempt(3),
    wait = wait_exponential_jitter(initial = 1, max = 8),
)
async def _get(client: httpx.AsyncClient, url: str) -> httpx.Response:
    return await client.get(url, headers = {"User-Agent": USER_AGENT})


async def _fetch_one(
    client: httpx.AsyncClient,
    title: str,
    url: str,
    *,
    progress: Progress,
    tier_name: str,
    framework_slug: str | None = None,
    store: Store | None = None,
) -> tuple[str, str, str, str] | None:
    """Returns (slug, url, body_markdown, title) on success, None on failure.
    Records progress + URL log internally."""
    t0 = time.monotonic()
    try:
        resp = await _get(client, url)
    except Exception as e:
        await progress.record_url(
            url,
            status = "fetch_error",
            tier = tier_name,
            fetch_ms = int((time.monotonic() - t0) * 1000),
            error_msg = f"{type(e).__name__}: {e}",
        )
        return None
    fetch_ms = int((time.monotonic() - t0) * 1000)
    if resp.status_code != 200:
        await progress.record_url(
            url,
            status = "http_error",
            tier = tier_name,
            http_code = resp.status_code,
            fetch_ms = fetch_ms,
            bytes_fetched = len(resp.content or b""),
            error_msg = f"HTTP {resp.status_code}",
        )
        return None
    raw = resp.text or ""
    if is_markdown_response(resp):
        body_md = raw
    else:
        if framework_slug and store is not None:
            try:
                raw, n_art = await extract_and_save_artifacts(
                    raw,
                    url,
                    slug = framework_slug,
                    store = store,
                    client = client,
                )
                if n_art:
                    logger.info(
                        f"[tier-2] {url}: saved {n_art} artifact(s) "
                        f"to ingestion/{framework_slug}/artifacts/"
                    )
            except Exception as e:
                logger.warning(
                    f"[tier-2] artifact extraction failed for {url}: "
                    f"{type(e).__name__}: {e}"
                )
        body_md = html_to_markdown(raw, source_url = url)
        if not title:
            title = extract_title(raw) or title
    if len(body_md.encode("utf-8")) < MIN_OK_BYTES:
        await progress.record_url(
            url,
            status = "extract_empty",
            tier = tier_name,
            http_code = resp.status_code,
            fetch_ms = fetch_ms,
            bytes_fetched = len(raw),
            extracted_chars = len(body_md),
            error_msg = "extracted body too short",
        )
        return None
    await progress.record_url(
        url,
        status = "success",
        tier = tier_name,
        http_code = resp.status_code,
        fetch_ms = fetch_ms,
        bytes_fetched = len(raw),
        extracted_chars = len(body_md),
    )
    slug = slugify(title or urlparse(url).path)
    return (slug, url, body_md, title or slug)


async def run(
    *,
    url: str,
    framework_slug: str,
    progress: Progress,
    store: Store,
) -> int:
    """Fetch index, fan out to N concurrent page fetches, write each to
    store. Returns the number of pages written. Raises RuntimeError if the
    index itself can't be fetched/parsed."""
    logger.info(f"[tier-2] framework={framework_slug} index={url}")
    await progress.start(tier = "llms_txt", total = 0)
    async with httpx.AsyncClient(
        timeout = httpx.Timeout(TIMEOUT_S, connect = 10.0),
        follow_redirects = True,
    ) as client:
        t0 = time.monotonic()
        try:
            resp = await _get(client, url)
        except Exception as e:
            err = f"{type(e).__name__}: {e}"
            await progress.record_url(
                url,
                status = "fetch_error",
                tier = "llms_txt",
                fetch_ms = int((time.monotonic() - t0) * 1000),
                error_msg = err,
            )
            await progress.finish(status = "failed")
            raise RuntimeError(f"Tier 2 index fetch failed for {url}: {err}")
        if resp.status_code != 200:
            await progress.record_url(
                url,
                status = "http_error",
                tier = "llms_txt",
                http_code = resp.status_code,
                fetch_ms = int((time.monotonic() - t0) * 1000),
                error_msg = f"HTTP {resp.status_code}",
            )
            await progress.finish(status = "failed")
            raise RuntimeError(f"Tier 2: {url} → HTTP {resp.status_code}")
        await progress.record_url(
            url,
            status = "success",
            tier = "llms_txt",
            http_code = resp.status_code,
            fetch_ms = int((time.monotonic() - t0) * 1000),
            bytes_fetched = len(resp.text or ""),
            extracted_chars = len(resp.text or ""),
        )
        links = parse_index(resp.text or "", base_url = url)
        if not links:
            logger.info(
                f"[tier-2] {url} parsed zero links — likely a long-form "
                f"prose llms.txt; signalling fallback"
            )
            raise EmptyLinksDetected(url)
        logger.info(f"[tier-2] parsed {len(links)} URLs from {url}")
        await progress.update_total(len(links))
        sem = asyncio.Semaphore(CONCURRENCY)
        written = 0

        async def _bound(title: str, link: str):
            nonlocal written
            async with sem:
                await progress.raise_if_cancelled()
                r = await _fetch_one(
                    client,
                    title,
                    link,
                    progress = progress,
                    tier_name = "llms_txt",
                    framework_slug = framework_slug,
                    store = store,
                )
            if r is not None:
                slug, src_url, body, t = r
                await store.add_page(
                    slug = slug,
                    url = src_url,
                    body = body,
                    tier = "llms_txt",
                    title = t,
                )
                written += 1
            await progress.update(current = written, last_url = link)
            return r
        await asyncio.gather(
            *(_bound(t, u) for t, u in links),
            return_exceptions = False,
        )
    if written == 0:
        await progress.finish(status = "failed")
        raise RuntimeError(f"Tier 2: {url} all {len(links)} pages failed")
    store.reorder_by_url_list([u for _, u in links])
    await progress.finish(status = "done")
    return written
