"""Tier 2 — fetch a `llms.txt` index, then each URL it points to.

`llms.txt` is the AnswerDotAI spec: a markdown file containing a structured
list of `- [Title](url): description` links to the documentation pages.
Parse the index, dedupe, then fetch each page concurrently (semaphore-bound)
and extract markdown. Pages that already serve markdown (Mintlify-style
`.md` URLs, `text/markdown` content-type) are passed through; HTML pages go
through the extractor.

Total page count is known after the index is parsed → progress switches
from indeterminate → determinate at that moment.
"""
import asyncio
import logging
import re
import time
from urllib.parse import urljoin, urlparse

import httpx
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential_jitter,
)

from .extract import extract_title, html_to_markdown
from .progress import Progress
from .store import Store


logger = logging.getLogger(__name__)


class EmptyLinksDetected(Exception):
    """Raised when llms.txt was fetched successfully but parsed zero
    usable per-page links. Some sites publish a long-form prose llms.txt
    (per the llmstxt.org spec) with bare-URL bullets like
    `- GitHub: https://github.com/…` instead of the `- [title](url)`
    markdown-link format our parser expects. In that case the dispatcher
    should fall through to the next available tier (sitemap/docs/github)
    rather than fail the run — Tier 2 simply isn't usable here."""
    pass


_USER_AGENT = "COELHONexus-DocsDistiller-Tier2/1.0"
_TIMEOUT_S = 30.0
_CONCURRENCY = 8
_MIN_OK_BYTES = 200


# Two link styles seen in llms.txt files:
#   A) `- [Title](https://url)`           — canonical markdown link (most sites)
#   B) `- Title (extra): https://url`    — bare-URL bullet (Supervision, others)
#       also matches `- Title: https://url` (no parens)
_LINK_MD_RE = re.compile(r"^\s*[-*]\s+\[([^\]]+)\]\(([^)]+)\)", re.MULTILINE)
_LINK_BARE_RE = re.compile(
    r"^\s*[-*]\s+(.+?):\s+(https?://\S+)\s*$", re.MULTILINE,
)


def _parse_index(body: str, base_url: str) -> list[tuple[str, str]]:
    """Return [(title, absolute_url), ...] from a llms.txt body. Tries the
    canonical markdown-link format first; then the bare-URL bullet format
    that Supervision (and likely others) use. Filters URLs to the same
    host as `base_url` so we don't try to ingest GitHub/PyPI/external
    meta-links that often appear in long-form llms.txt files. Dedupes
    while preserving first-occurrence order."""
    from urllib.parse import urlparse
    base_host = (urlparse(base_url).netloc or "").lower()
    out: list[tuple[str, str]] = []
    seen: set[str] = set()

    def _add(title: str, url: str) -> None:
        url = urljoin(base_url, url.strip())
        # Drop external links — long-form llms.txt usually peppers in
        # meta-pointers like `- GitHub: https://github.com/...` that
        # aren't docs pages and would route through the wrong tier.
        host = (urlparse(url).netloc or "").lower()
        if base_host and host and host != base_host:
            return
        if url in seen:
            return
        seen.add(url)
        out.append((title.strip(), url))

    for m in _LINK_MD_RE.finditer(body):
        _add(m.group(1), m.group(2))
    for m in _LINK_BARE_RE.finditer(body):
        _add(m.group(1), m.group(2))
    return out


def _slugify(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")[:80] or "page"


@retry(
    reraise=True,
    retry=retry_if_exception_type((httpx.TimeoutException, httpx.NetworkError)),
    stop=stop_after_attempt(3),
    wait=wait_exponential_jitter(initial=1, max=8),
)
async def _get(client: httpx.AsyncClient, url: str) -> httpx.Response:
    return await client.get(url, headers={"User-Agent": _USER_AGENT})


def _is_markdown_response(resp: httpx.Response) -> bool:
    ctype = (resp.headers.get("content-type") or "").lower()
    return (
        "text/markdown" in ctype
        or "text/x-markdown" in ctype
        or resp.url.path.endswith(".md")
    )


async def _fetch_one(
    client: httpx.AsyncClient,
    title: str,
    url: str,
    *,
    progress: Progress,
    tier_name: str,
) -> tuple[str, str, str, str] | None:
    """Returns (slug, url, body_markdown, title) on success, None on failure.
    Records progress + URL log internally."""
    t0 = time.monotonic()
    try:
        resp = await _get(client, url)
    except Exception as e:
        await progress.record_url(
            url, status="fetch_error", tier=tier_name,
            fetch_ms=int((time.monotonic() - t0) * 1000),
            error_msg=f"{type(e).__name__}: {e}",
        )
        return None

    fetch_ms = int((time.monotonic() - t0) * 1000)
    if resp.status_code != 200:
        await progress.record_url(
            url, status="http_error", tier=tier_name,
            http_code=resp.status_code, fetch_ms=fetch_ms,
            bytes_fetched=len(resp.content or b""),
            error_msg=f"HTTP {resp.status_code}",
        )
        return None

    raw = resp.text or ""
    if _is_markdown_response(resp):
        body_md = raw
    else:
        body_md = html_to_markdown(raw, source_url=url)
        if not title:
            title = extract_title(raw) or title

    if len(body_md.encode("utf-8")) < _MIN_OK_BYTES:
        await progress.record_url(
            url, status="extract_empty", tier=tier_name,
            http_code=resp.status_code, fetch_ms=fetch_ms,
            bytes_fetched=len(raw), extracted_chars=len(body_md),
            error_msg="extracted body too short",
        )
        return None

    await progress.record_url(
        url, status="success", tier=tier_name,
        http_code=resp.status_code, fetch_ms=fetch_ms,
        bytes_fetched=len(raw), extracted_chars=len(body_md),
    )
    slug = _slugify(title or urlparse(url).path)
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
    await progress.start(tier="llms_txt", total=0)  # indeterminate until parse

    async with httpx.AsyncClient(
        timeout=httpx.Timeout(_TIMEOUT_S, connect=10.0),
        follow_redirects=True,
    ) as client:
        # Fetch index
        t0 = time.monotonic()
        try:
            resp = await _get(client, url)
        except Exception as e:
            err = f"{type(e).__name__}: {e}"
            await progress.record_url(
                url, status="fetch_error", tier="llms_txt",
                fetch_ms=int((time.monotonic() - t0) * 1000),
                error_msg=err,
            )
            await progress.finish(status="failed")
            raise RuntimeError(f"Tier 2 index fetch failed for {url}: {err}")

        if resp.status_code != 200:
            await progress.record_url(
                url, status="http_error", tier="llms_txt",
                http_code=resp.status_code,
                fetch_ms=int((time.monotonic() - t0) * 1000),
                error_msg=f"HTTP {resp.status_code}",
            )
            await progress.finish(status="failed")
            raise RuntimeError(f"Tier 2: {url} → HTTP {resp.status_code}")

        await progress.record_url(
            url, status="success", tier="llms_txt",
            http_code=resp.status_code,
            fetch_ms=int((time.monotonic() - t0) * 1000),
            bytes_fetched=len(resp.text or ""),
            extracted_chars=len(resp.text or ""),
        )
        links = _parse_index(resp.text or "", base_url=url)
        if not links:
            # Don't mark as failed — dispatcher will catch + fall through.
            logger.info(
                f"[tier-2] {url} parsed zero links — likely a long-form "
                f"prose llms.txt; signalling fallback"
            )
            raise EmptyLinksDetected(url)

        logger.info(f"[tier-2] parsed {len(links)} URLs from {url}")
        await progress.update_total(len(links))

        # Fan out — stream each successful fetch to MinIO inside the
        # coroutine. Bounded RAM, partial-persistence on crash, smooth
        # progress bar. See tier3_sitemap for the broader rationale.
        sem = asyncio.Semaphore(_CONCURRENCY)
        written = 0

        async def _bound(title: str, link: str):
            nonlocal written
            async with sem:
                await progress.raise_if_cancelled()
                r = await _fetch_one(
                    client, title, link, progress=progress, tier_name="llms_txt",
                )
            if r is not None:
                slug, src_url, body, t = r
                await store.add_page(
                    slug=slug, url=src_url, body=body,
                    tier="llms_txt", title=t,
                )
                written += 1
            await progress.update(current=written, last_url=link)
            return r

        await asyncio.gather(
            *(_bound(t, u) for t, u in links),
            return_exceptions=False,
        )

    if written == 0:
        await progress.finish(status="failed")
        raise RuntimeError(f"Tier 2: {url} all {len(links)} pages failed")

    await progress.finish(status="done")
    return written
