"""Fetch a single llms-full.txt bundle. Raises ManifestDetected when the body is a link index (few fences + many URL: pointers), and TierUnavailable when the URL gives nothing usable (404, a server error that outlasts the retries, an empty body, an HTML page) — either way the dispatcher falls through to the next declared tier."""
from __future__ import annotations
import domains, infra
from . import domain, params

import logging
import time
from urllib.parse import urlparse
import uuid

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


async def _fetch_nav(client: httpx.AsyncClient, nav_url: str) -> httpx.Response | None:
    """The site's own page index (its `llms.txt`), which gives the pages their order and sections. Best effort — a missing, slow
    or odd index gives `None` and the bundle's order stands."""
    try:
        resp = await client.get(nav_url, headers = {"User-Agent": params.USER_AGENT}, timeout = params.NAV_TIMEOUT_S)
    except Exception as e:  # noqa: BLE001
        logger.info(f"[tier-1] no navigation order from {nav_url}: {type(e).__name__}: {e}")
        return None
    if resp.status_code != 200 or domain.looks_like_html(resp.text):
        return None
    return resp


async def _capture(
    minio: infra.minio.service.MinIOStorage,
    *,
    framework_slug: str,
    url: str,
    parts: list[httpx.Response],
    nav_url: str | None,
    nav: httpx.Response | None,
    path_filter: dict | None,
    run_id: str | None = None,
) -> domains.dd.ingestion.capture.entities.Capture:
    """Keep what was fetched exactly as it came (the bundle — each part of a paginated one — and the index when there is one) under
    `ingestion/raw/`: the input every transform of this bundle starts from, so changing the transform never needs the site again."""
    raw = domains.dd.ingestion.capture
    files, texts = [], {}
    for number, part in enumerate(parts, start = 1):
        name = domain.part_file_name(number)
        files.append(raw.domain.captured_file(
            name = name, role = raw.params.ROLE_PAGE, url = str(part.request.url) if number > 1 else url, final_url = str(part.url),
            status = part.status_code, content_type = part.headers.get("content-type", ""), text = part.text,
        ))
        texts[name] = part.text
    if nav is not None and nav_url:
        files.append(raw.domain.captured_file(
            name = params.NAV_FILE, role = raw.params.ROLE_INDEX, url = nav_url, final_url = str(nav.url),
            status = nav.status_code, content_type = nav.headers.get("content-type", ""), text = nav.text,
        ))
        texts[params.NAV_FILE] = nav.text
    kept = raw.domain.new_capture(
        framework_slug, params.TIER_KIND, url, files, capture_params = {"path_filter": path_filter, "nav_url": nav_url, "run_id": run_id or uuid.uuid4().hex},
    )
    await raw.service.save(minio, kept, texts)
    return kept


async def _follow_parts(client: httpx.AsyncClient, url: str, first: httpx.Response) -> tuple[list[httpx.Response], str | None]:
    """`first` and, when it says it is part of a paginated bundle (GitBook's "Next part: …"), every part after it, in order — a bundle
    that is only its first part is half the docs. Returns `(parts, why)`: `why` is set (and `parts` is what was got) when a part
    could not be had, so the caller can refuse the whole bundle instead of keeping a gap; a part may be short (the last often is)
    but not an error or an HTML page."""
    parts = [first]
    seen = {url}
    while len(parts) < params.MAX_PARTS:
        target = domain.next_part_url(parts[-1].text or "", str(parts[-1].url))
        if target is None or target in seen:
            break
        seen.add(target)
        try:
            part = await _fetch(client, target)
        except Exception as e:  # noqa: BLE001 — a timeout that outlasted the retries is a part that could not be had
            return parts, f"part {len(parts) + 1} ({target}): {type(e).__name__}: {e}"
        reason = domain.unusable_reason(part.status_code, part.text or "", min_bytes = 1)
        if reason:
            return parts, f"part {len(parts) + 1} ({target}): {reason}"
        parts.append(part)
    return parts, None


async def capture(
    minio: infra.minio.service.MinIOStorage,
    *,
    url: str,
    framework_slug: str,
    path_filter: dict | None = None,
    nav_url: str | None = None,
    run_id: str | None = None,
) -> domains.dd.ingestion.capture.entities.Capture:
    """Fetch the bundle (and the site's index) and keep it as it came — no page is made, nothing is stored but the capture: what the
    A/B harness starts a transform from. `run` does the same on the way to making pages. Raises TierUnavailable for an unusable URL."""
    async with httpx.AsyncClient(
        timeout = httpx.Timeout(params.TIMEOUT_S, connect = params.CONNECT_TIMEOUT_S),
        follow_redirects = True,
    ) as client:
        resp = await _fetch(client, url)
        reason = domain.unusable_reason(resp.status_code, resp.text or "")
        if reason:
            raise domains.dd.ingestion.tiers.errors.TierUnavailable(f"{url}: {reason}")
        parts, gap = await _follow_parts(client, url, resp)
        if gap:
            raise domains.dd.ingestion.tiers.errors.TierUnavailable(f"{url}: {gap}")
        nav = await _fetch_nav(client, nav_url) if nav_url else None
        return await _capture(
            minio, framework_slug = framework_slug, url = url, parts = parts, nav_url = nav_url, nav = nav, path_filter = path_filter, run_id = run_id,
        )


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
        parts, gap = await _follow_parts(client, url, resp)
        if gap:
            await _give_up(
                progress, url, status = "http_error", reason = gap,
                http_code = resp.status_code, fetch_ms = fetch_ms, bytes_fetched = len(body),
            )
        body = domain.join_parts([part.text for part in parts])  # each part's `.text` stays what the site sent
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
        # The index only gives a split bundle its order and sections: a bundle that stays one page does not ask for it.
        nav = await _fetch_nav(client, nav_url) if nav_url and will_split else None
        await _capture(
            store.minio, framework_slug = framework_slug, url = url, parts = parts, nav_url = nav_url, nav = nav, path_filter = path_filter,
            run_id = store.run_id,
        )
        if nav_url and will_split:
            store.nav_index = domain.nav_index(nav.text if nav is not None else "", nav_url)
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
            # The capture above holds the bundle as it came; a second copy of it is not kept next to the pages.
            keep_raw = False,
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
