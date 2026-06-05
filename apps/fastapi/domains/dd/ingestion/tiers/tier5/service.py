"""Tier 5 — GitHub README-only crawler.

For frameworks whose docs home IS the GitHub repo (no docs site, no GitHub
Pages). Walks the repo tree once via the GitHub API, then fans out parallel
GETs to raw.githubusercontent.com for every `.md` / `.mdx` blob.

Pipeline (~5 s on a small repo, vs minutes for Tier 4 BFS on the same view):

  1. Resolve default branch          (api.github.com/repos/{org}/{repo})
  2. List tree recursively            (.../git/trees/{branch}?recursive=1)
  3. Filter to docs blobs (.md / .mdx; skip CI/vendor/test/build/locale dirs)
  4. Parallel raw.githubusercontent.com GETs (sem=10)
  5. Write each body to the store

Auth: set GITHUB_TOKEN to lift the API rate limit from 60/h to 5,000/h. The
raw CDN ignores limits regardless.
"""
import asyncio
import logging
import os
import time
from typing import Optional

import httpx
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential_jitter,
)

from ...progress import Progress
from ...storage import Store
from .domain import is_docs_blob, parse_repo, slug_from_path
from .params import (
    API_BASE,
    CONCURRENCY,
    MIN_OK_BYTES,
    RAW_BASE,
    TIMEOUT_S,
    USER_AGENT,
)


logger = logging.getLogger(__name__)


def _auth_headers() -> dict[str, str]:
    h = {"User-Agent": USER_AGENT, "Accept": "application/vnd.github+json"}
    tok = os.environ.get("GITHUB_TOKEN", "").strip()
    if tok:
        h["Authorization"] = f"Bearer {tok}"
    return h


@retry(
    reraise = True,
    retry = retry_if_exception_type((httpx.TimeoutException, httpx.NetworkError)),
    stop = stop_after_attempt(3),
    wait = wait_exponential_jitter(initial = 1, max = 15),
)
async def _get_json(client: httpx.AsyncClient, url: str) -> dict:
    r = await client.get(url, headers = _auth_headers())
    if r.status_code != 200:
        raise RuntimeError(f"GitHub API {url} → HTTP {r.status_code}")
    return r.json()


async def _default_branch(
    client: httpx.AsyncClient, org: str, repo: str,
) -> str:
    data = await _get_json(client, f"{API_BASE}/repos/{org}/{repo}")
    return data.get("default_branch") or "main"


async def _list_blobs(
    client: httpx.AsyncClient, org: str, repo: str, branch: str,
) -> list[tuple[str, Optional[int]]]:
    """Return [(path, size_bytes?), ...] for every blob in the repo tree."""
    data = await _get_json(
        client,
        f"{API_BASE}/repos/{org}/{repo}/git/trees/{branch}?recursive=1",
    )
    tree = data.get("tree") or []
    out: list[tuple[str, Optional[int]]] = []
    for node in tree:
        if node.get("type") != "blob":
            continue
        path = node.get("path") or ""
        size = node.get("size")
        if path:
            out.append((path, size))
    if data.get("truncated"):
        logger.warning(
            f"[tier-5] {org}/{repo} tree was truncated by GitHub — "
            f"large monorepos may miss some blobs"
        )
    return out


@retry(
    reraise = True,
    retry = retry_if_exception_type((httpx.TimeoutException, httpx.NetworkError)),
    stop = stop_after_attempt(3),
    wait = wait_exponential_jitter(initial = 1, max = 8),
)
async def _fetch_blob(
    client: httpx.AsyncClient, org: str, repo: str, branch: str, path: str,
) -> httpx.Response:
    url = f"{RAW_BASE}/{org}/{repo}/{branch}/{path}"
    return await client.get(url, headers = {"User-Agent": USER_AGENT})


async def _fetch_one(
    client: httpx.AsyncClient,
    org: str, repo: str, branch: str,
    path: str,
    *,
    progress: Progress,
) -> tuple[str, str, str, str] | None:
    raw_url = f"{RAW_BASE}/{org}/{repo}/{branch}/{path}"
    t0 = time.monotonic()
    try:
        resp = await _fetch_blob(client, org, repo, branch, path)
    except Exception as e:
        await progress.record_url(
            raw_url,
            status = "fetch_error",
            tier = "github",
            fetch_ms = int((time.monotonic() - t0) * 1000),
            error_msg = f"{type(e).__name__}: {e}",
        )
        return None
    fetch_ms = int((time.monotonic() - t0) * 1000)
    if resp.status_code != 200:
        await progress.record_url(
            raw_url,
            status = "http_error",
            tier = "github",
            http_code = resp.status_code,
            fetch_ms = fetch_ms,
            error_msg = f"HTTP {resp.status_code}",
        )
        return None
    body = resp.text or ""
    if len(body.encode("utf-8")) < MIN_OK_BYTES:
        await progress.record_url(
            raw_url,
            status = "extract_empty",
            tier = "github",
            http_code = resp.status_code,
            fetch_ms = fetch_ms,
            bytes_fetched = len(body),
            extracted_chars = len(body),
            error_msg = f"body too short ({len(body)}B)",
        )
        return None
    await progress.record_url(
        raw_url,
        status = "success",
        tier = "github",
        http_code = resp.status_code,
        fetch_ms = fetch_ms,
        bytes_fetched = len(body),
        extracted_chars = len(body),
    )
    slug = slug_from_path(path)
    title = path
    return (slug, raw_url, body, title)


async def run(
    *,
    url: str,
    framework_slug: str,
    progress: Progress,
    store: Store,
) -> int:
    parsed = parse_repo(url)
    if not parsed:
        raise RuntimeError(f"Tier 5: not a github.com repo URL: {url!r}")
    org, repo = parsed
    logger.info(f"[tier-5] framework={framework_slug} repo={org}/{repo}")
    await progress.start(tier = "github", total = 0)
    async with httpx.AsyncClient(
        timeout = httpx.Timeout(TIMEOUT_S, connect = 10.0),
        follow_redirects = True,
    ) as client:
        t0 = time.monotonic()
        try:
            branch = await _default_branch(client, org, repo)
        except Exception as e:
            err = f"{type(e).__name__}: {e}"
            await progress.record_url(
                f"{API_BASE}/repos/{org}/{repo}",
                status = "fetch_error",
                tier = "github",
                fetch_ms = int((time.monotonic() - t0) * 1000),
                error_msg = err,
            )
            await progress.finish(status = "failed")
            raise RuntimeError(f"Tier 5: repo lookup failed: {err}")
        await progress.record_url(
            f"{API_BASE}/repos/{org}/{repo}",
            status = "success",
            tier = "github",
            fetch_ms = int((time.monotonic() - t0) * 1000),
        )

        t0 = time.monotonic()
        try:
            blobs = await _list_blobs(client, org, repo, branch)
        except Exception as e:
            err = f"{type(e).__name__}: {e}"
            await progress.record_url(
                f"{API_BASE}/repos/{org}/{repo}/git/trees/{branch}",
                status = "fetch_error",
                tier = "github",
                fetch_ms = int((time.monotonic() - t0) * 1000),
                error_msg = err,
            )
            await progress.finish(status = "failed")
            raise RuntimeError(f"Tier 5: tree fetch failed: {err}")
        await progress.record_url(
            f"{API_BASE}/repos/{org}/{repo}/git/trees/{branch}",
            status = "success",
            tier = "github",
            fetch_ms = int((time.monotonic() - t0) * 1000),
        )
        keep: list[str] = [
            path for path, _size in blobs if is_docs_blob(path)
        ]
        if not keep:
            await progress.finish(status = "failed")
            raise RuntimeError(
                f"Tier 5: {org}/{repo}@{branch} has no .md/.mdx docs blobs "
                f"after filter ({len(blobs)} total blobs in tree)"
            )
        logger.info(
            f"[tier-5] {len(blobs)} blobs → {len(keep)} docs after filter "
            f"(branch={branch})"
        )
        await progress.update_total(len(keep))
        sem = asyncio.Semaphore(CONCURRENCY)
        written = 0

        async def _bound(p: str):
            nonlocal written
            async with sem:
                await progress.raise_if_cancelled()
                r = await _fetch_one(
                    client, org, repo, branch, p, progress = progress,
                )
            if r is not None:
                slug, raw_url, body, title = r
                await store.add_page(
                    slug = slug,
                    url = raw_url,
                    body = body,
                    tier = "github",
                    title = title,
                )
                written += 1
            await progress.update(current = written, last_url = p)
            return r

        await asyncio.gather(
            *(_bound(p) for p in keep),
            return_exceptions = False,
        )
    if written == 0:
        await progress.finish(status = "failed")
        raise RuntimeError(f"Tier 5: {org}/{repo} all blob fetches failed")
    await progress.finish(status = "done")
    return written
