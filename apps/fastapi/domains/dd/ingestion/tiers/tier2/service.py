"""Fetch llms.txt index (AnswerDotAI spec), then all linked pages concurrently. Markdown responses pass through; HTML goes through the extractor. Progress becomes determinate after index parse."""
from __future__ import annotations
import domains
from . import domain, params

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


logger = logging.getLogger(__name__)


@retry(
    reraise = True,
    retry = retry_if_exception_type((httpx.TimeoutException, httpx.NetworkError)),
    stop = stop_after_attempt(3),
    wait = wait_exponential_jitter(initial = 1, max = 8),
)
async def _get(client: httpx.AsyncClient, url: str) -> httpx.Response:
    return await client.get(url, headers = {"User-Agent": params.USER_AGENT})


async def _fetch_one(
    client: httpx.AsyncClient,
    title: str,
    url: str,
    *,
    progress: domains.dd.ingestion.runtime.progress.service.Progress,
    tier_name: str,
    framework_slug: str | None = None,
    store: domains.dd.ingestion.storage.service.Store | None = None,
    index_host: str = "",
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
    cross_host = bool(index_host) and (urlparse(url).netloc or "").lower() != index_host
    if domain.is_markdown_response(resp):
        body_md = raw
        if cross_host:
            # Gist/raw mirrors prepend the filename as a debris H1 (observed:
            # `starlette-sml.md` starts with a literal `# index.md` line) —
            # strip it so it never seeds a junk heading downstream.
            body_md = domain.strip_filename_h1(body_md, url)
    elif resp.url.path.lower().endswith(".txt"):
        # Plain-text index (e.g. `apilist.txt`) — no HTML to extract, keep
        # verbatim so the function list survives intact.
        body_md = raw
    elif resp.url.path.lower().endswith(".py"):
        # Raw example source (linked straight from the index, not HTML) —
        # fence it so vault fence extraction + digest code_refs see a real
        # code block instead of flat prose.
        body_md = f"```python\n{(raw or '').strip()}\n```\n"
    else:
        if framework_slug and store is not None:
            try:
                raw, n_art = await domains.dd.ingestion.artifacts.service.extract_and_save_artifacts(
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
        body_md = domains.dd.ingestion.tiers.extract.domain.html_to_markdown(raw, source_url = url)
        if not title:
            title = domains.dd.ingestion.tiers.extract.domain.extract_title(raw) or title
    if len(body_md.encode("utf-8")) < params.MIN_OK_BYTES:
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
    slug = domain.slugify(title or urlparse(url).path)
    return (slug, url, body_md, title or slug)


async def run(
    *,
    url: str,
    framework_slug: str,
    progress: domains.dd.ingestion.runtime.progress.service.Progress,
    store: domains.dd.ingestion.storage.service.Store,
) -> int:
    """Fetch index, fan out to N concurrent page fetches, write each to
    store. Returns the number of pages written. Raises RuntimeError if the
    index itself can't be fetched/parsed."""
    logger.info(f"[tier-2] framework={framework_slug} index={url}")
    await progress.start(tier = "llms_txt", total = 0)
    async with httpx.AsyncClient(
        timeout = httpx.Timeout(params.TIMEOUT_S, connect = 10.0),
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
        links = domain.parse_index(resp.text or "", base_url = url)
        if not links:
            logger.info(
                f"[tier-2] {url} parsed zero links — likely a long-form "
                f"prose llms.txt; signalling fallback"
            )
            raise domains.dd.ingestion.tiers.errors.EmptyLinksDetected(url)
        # Author-written index summary (spec v2 blockquote) — stashed on the
        # store for dispatch.finalize; "" when the index carries none.
        try:
            store.index_summary = domain.parse_index_summary(resp.text or "")
        except Exception:
            store.index_summary = ""
        logger.info(f"[tier-2] parsed {len(links)} URLs from {url}")
        await progress.update_total(len(links))
        index_host = (urlparse(url).netloc or "").lower()
        sem = asyncio.Semaphore(params.CONCURRENCY)
        slug_lock = asyncio.Lock()
        slug_owner: dict[str, str] = {}
        written = 0

        async def _claim_slug(base: str, link: str) -> str:
            """Deterministic collision-free slug within this run (mirror of
            Tier 1's stable `host_slug` philosophy: same input → same slug,
            no silent overwrites). First claimant keeps the bare slug;
            later same-title pages suffix the parent path segment
            (`quantization` → `quantization-usage-guides`,
            `module-overview` → `module-overview-module-3`), counter as final
            fallback. Keys were already unique (idx-prefixed), so this only
            cleans manifests/explorer titles — no storage layout change."""
            async with slug_lock:
                if base not in slug_owner:
                    slug_owner[base] = link
                    return base
                if slug_owner[base] == link:
                    return base
                try:
                    raw_parts = [p for p in urlparse(link).path.split("/") if p]
                    # Drop a trailing file segment (`index.md`, `foo.md`) — the
                    # page's own filename re-slugifies to `base` itself and
                    # would yield a redundant `slug-slug` suffix. Then walk
                    # ancestors outward, taking the first segment that actually
                    # distinguishes (`.../private-cloud/configuration/` →
                    # `private-cloud`, not the `configuration` dir itself).
                    if raw_parts and raw_parts[-1].lower().endswith(".md"):
                        raw_parts = raw_parts[:-1]
                    stem = ""
                    for seg in reversed(raw_parts):
                        cand_seg = domain.slugify(seg)
                        if cand_seg and cand_seg != base:
                            stem = cand_seg
                            break
                    if not stem:
                        stem = (urlparse(link).netloc or "x").split(".")[0]
                        stem = domain.slugify(stem) or "x"
                except Exception:
                    stem = "x"
                cand = f"{base}-{stem}"[:80]
                i = 2
                while cand in slug_owner and slug_owner[cand] != link:
                    cand = f"{base}-{stem}-{i}"[:80]
                    i += 1
                slug_owner[cand] = link
                return cand

        async def _bound(title: str, link: str, section: str = "", notes: str = ""):
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
                    index_host = index_host,
                )
            if r is not None:
                slug, src_url, body, t = r
                # Redirect/archived stub (shape guard, Tier-2-only mirror of
                # Tier 1's manifest check) — recorded, never stored.
                if domain.looks_like_redirect_stub(body):
                    await progress.record_url(
                        link,
                        status = "redirect_stub",
                        tier = "llms_txt",
                        bytes_fetched = len((body or "").encode("utf-8")),
                        error_msg = "redirect/archived stub pointer, skipped",
                    )
                # Tier-2-gated stub floor (~1KB): nav fragments / course
                # scaffolding carry no study signal and each burns a full
                # judge+distill cycle downstream. Tier 1 untouched — its own
                # post.split path keeps the 300B floor.
                elif len((body or "").encode("utf-8")) < params.STUB_MIN_BYTES:
                    await progress.record_url(
                        link,
                        status = "stub_dropped",
                        tier = "llms_txt",
                        bytes_fetched = len((body or "").encode("utf-8")),
                        error_msg = "tier-2 stub (<1KB), skipped",
                    )
                else:
                    slug = await _claim_slug(slug, src_url)
                    await store.add_page(
                        slug = slug,
                        url = src_url,
                        body = body,
                        tier = "llms_txt",
                        title = t,
                        section = section,
                        notes = notes,
                    )
                    written += 1
            await progress.update(current = written, last_url = link)
            return r
        await asyncio.gather(
            *(_bound(t, u, s, n) for t, u, s, n in links),
            return_exceptions = False,
        )
    if written == 0:
        await progress.finish(status = "failed")
        raise RuntimeError(f"Tier 2: {url} all {len(links)} pages failed")
    store.reorder_by_url_list([u for _, u, _, _ in links])
    await progress.finish(status = "done")
    return written
