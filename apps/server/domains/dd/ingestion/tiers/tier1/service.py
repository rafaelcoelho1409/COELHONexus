"""Fetch a single llms-full.txt bundle. Raises ManifestDetected when the body is a link index (few fences + many URL: pointers), and TierUnavailable when the URL gives nothing usable (404, a server error that outlasts the retries, an empty body, an HTML page) — either way the dispatcher falls through to the next declared tier."""
from __future__ import annotations
import domains
from . import domain, params

import logging
import time
from urllib.parse import urlparse

import httpx


logger = logging.getLogger(__name__)


async def _fetch(client: httpx.AsyncClient, url: str) -> httpx.Response:
    """GET with retries on timeouts, network errors and transient statuses (the shared fetch). The last response is
    returned for any other status — and for a transient one that outlasts the retries — so the caller decides what a
    404 or a 503 means; a timeout or network error that outlasts them raises."""
    return await domains.dd.ingestion.tiers.fetch.service.get(
        client,
        url,
        headers = {"User-Agent": params.USER_AGENT},
        attempts = params.RETRY_ATTEMPTS,
        wait_initial = params.RETRY_WAIT_INITIAL_S,
        wait_max = params.RETRY_WAIT_MAX_S,
        retry_after_cap = params.RETRY_AFTER_MAX_S,
    )


async def _nav_index(client: httpx.AsyncClient, nav_url: str) -> list[tuple[str, str]]:
    """The site's own page order and sections: `[(url, section), ...]` as its `llms.txt` lists them, top to bottom (its
    navigation). Best effort — a missing, slow or odd index gives `[]` and the bundle's order stands."""
    try:
        resp = await client.get(nav_url, headers = {"User-Agent": params.USER_AGENT}, timeout = params.NAV_TIMEOUT_S)
        if resp.status_code != 200 or domain.looks_like_html(resp.text):
            return []
        links = domains.dd.ingestion.tiers.tier2.domain.parse_index(resp.text, nav_url)
        return [(url, section) for _title, url, section, _notes in links]
    except Exception as e:  # noqa: BLE001
        logger.info(f"[tier-1] no navigation order from {nav_url}: {type(e).__name__}: {e}")
        return []


async def _give_up(
    progress: domains.dd.ingestion.runtime.progress.service.Progress,
    url: str,
    *,
    status: str,
    reason: str,
    **record,
) -> None:
    """Log why this URL cannot be used and hand over to the next tier. Marked `downgrade`, not `failed`: the run
    is not over, and only the dispatcher decides when it is."""
    await progress.record_url(url, status = status, tier = params.TIER_KIND, error_msg = reason, **record)
    await progress.finish(status = "downgrade")
    raise domains.dd.ingestion.tiers.errors.TierUnavailable(f"{url}: {reason}")


async def run(
    *,
    url: str,
    framework_slug: str,
    progress: domains.dd.ingestion.runtime.progress.service.Progress,
    store: domains.dd.ingestion.storage.service.Store,
    path_filter: dict | None = None,
    nav_url: str | None = None,
) -> int:
    """Fetch bundle, write one page, return 1. Raises ManifestDetected if the body is a link index and TierUnavailable if the URL is unusable. `path_filter` (the catalog's `include`/`exclude`) trims a bundle that covers more than the framework. `nav_url` (the site's `llms.txt`) gives the order and the sections its pages are listed in, which the split then follows."""
    host = (urlparse(url).netloc or "").lower()
    if not host:
        raise RuntimeError(f"Tier 1: cannot parse host from url={url!r}")
    logger.info(f"[tier-1] framework={framework_slug} url={url}")
    await progress.start(tier = params.TIER_KIND, total = 1)
    await progress.raise_if_cancelled()
    async with httpx.AsyncClient(
        timeout = httpx.Timeout(params.TIMEOUT_S, connect = params.CONNECT_TIMEOUT_S),
        follow_redirects = True,
    ) as client:
        t0 = time.monotonic()
        try:
            resp = await _fetch(client, url)
        except Exception as e:
            await _give_up(
                progress, url, status = "fetch_error", reason = f"{type(e).__name__}: {e}",
                fetch_ms = int((time.monotonic() - t0) * 1000),
            )
        fetch_ms = int((time.monotonic() - t0) * 1000)
        body = resp.text or ""
        if resp.status_code != 200:
            await _give_up(
                progress, url, status = "http_error", reason = f"HTTP {resp.status_code}",
                http_code = resp.status_code, fetch_ms = fetch_ms, bytes_fetched = len(body),
            )
        if len(body) < params.MIN_OK_BYTES:
            await _give_up(
                progress, url, status = "extract_empty", reason = f"body too short ({len(body)}B)",
                http_code = resp.status_code, fetch_ms = fetch_ms, bytes_fetched = len(body),
                extracted_chars = 0,
            )
        if domain.looks_like_html(body):
            await _give_up(
                progress, url, status = "extract_empty", reason = "HTML page instead of a markdown bundle",
                http_code = resp.status_code, fetch_ms = fetch_ms, bytes_fetched = len(body),
                extracted_chars = 0,
            )

        is_manifest, stats = domain.looks_like_manifest(body)
        if is_manifest:
            logger.warning(
                f"[tier-1] {url} looks like a manifest "
                f"(fences={stats['fences']}, urls={stats['urls']}, "
                f"md_pointers={stats['md_pointers']}) — falling to the next tier"
            )
            await progress.record_url(
                url,
                status = "downgrade",
                tier = params.TIER_KIND,
                http_code = resp.status_code,
                fetch_ms = fetch_ms,
                bytes_fetched = len(body),
                error_msg = "manifest detected; falling to the next tier",
            )
            await progress.finish(status = "downgrade")
            raise domains.dd.ingestion.tiers.errors.ManifestDetected(f"{url}: {stats}")
        fetched_chars = len(body)
        body, kept, total = domain.apply_path_filter(body, path_filter)
        if total:
            logger.info(f"[tier-1] {framework_slug}: path_filter kept {kept} of {total} sections")
            if kept == 0:
                await _give_up(
                    progress, url, status = "extract_empty",
                    reason = f"path_filter kept none of the bundle's {total} sections",
                    http_code = resp.status_code, fetch_ms = fetch_ms, bytes_fetched = fetched_chars,
                    extracted_chars = 0,
                )
        slug = domain.host_slug(host)
        will_split = domains.dd.ingestion.post.domain.will_be_split(body)
        if nav_url and will_split:
            store.nav_index = await _nav_index(client, nav_url)
        await store.add_page(
            slug = slug,
            url = url,
            body = body,
            tier = params.TIER_KIND,
            # Its own heading when it stays one page; a bundle big enough to be split is replaced by pages that carry
            # their own titles.
            title = domains.dd.ingestion.post.domain.display_title(body, slug),
            # That same bundle is about to be split into pages, each of which gets its own vault afterwards; one
            # built for the whole bundle is thrown away (seconds of work and ~10 MB left in storage).
            defer_vault = will_split,
            # ...and each of those pages resolves its `./x` links against its own URL when it is split out.
            relative_links = not will_split,
        )
        await progress.record_url(
            url,
            status = "success",
            tier = params.TIER_KIND,
            http_code = resp.status_code,
            fetch_ms = fetch_ms,
            bytes_fetched = fetched_chars,
            extracted_chars = len(body),
        )
        # No `finish` here: the run is not over. Splitting, the vault backfill and finalizing come next, and the
        # dispatcher alone marks the run done — a status of "done" mid-run had the UI close its progress box early.
        await progress.update(current = 1, last_url = url)
        return 1
