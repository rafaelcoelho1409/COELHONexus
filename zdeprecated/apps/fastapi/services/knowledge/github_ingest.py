"""
Knowledge Distiller — Tier-GH Ingestion (GitHub README-only repos)

Dispatched by `services/knowledge/ingestion.py` when the resolver's GitHub
discovery returned `source_signals.github_discover == "readme_only"` — meaning
the project's docs home IS the GitHub repo itself (no dedicated docs site and
no GitHub Pages).

PIPELINE (~5s total for typical repos, vs ~20 min for Tier 4 Playwright on
the same page-tree view):
  1. GET `api.github.com/repos/{org}/{repo}/git/trees/{default_branch}?recursive=1`
     → one API call, returns the full repo tree
  2. Filter for markdown blobs (*.md, *.mdx), excluding vendored / CI / test
     directories that add noise without docs value
  3. Parallel GET `raw.githubusercontent.com/{org}/{repo}/{branch}/{path}` for
     each filtered blob (CDN-cached, effectively no rate limit)
  4. `_write_raw()` each result to MinIO at `<study_root>/research/raw/<slug>.md`
     with uniform ManifestEntry shape so the downstream distiller doesn't care
     which tier produced the corpus.

AUTH: set `GITHUB_TOKEN` env var to bump the API limit from 60/hr (anonymous)
to 5000/hr. Without a token the tree call still works for public repos but
at the low limit — fine for dev, insufficient for production bursts.

RESILIENCE:
  - Retries via `tenacity` on 429 / 5xx / timeout (max 3, exp jitter, capped
    at 30s). Not needed for raw.githubusercontent.com (CDN-cached) but keeps
    the tree call robust against transient GitHub API hiccups.
  - asyncio.Semaphore(_MAX_CONCURRENT) caps parallel raw fetches — GitHub
    doesn't throttle raw.githubusercontent.com meaningfully but polite
    concurrency avoids accidental ELB saturation on our own egress.
  - Partial-failure policy matches Tier 4: log each miss, continue ingesting
    the rest. Abort only if ≥50% of blobs fail.

OUTPUT: same MinIO layout as Tier 4:
  <study_root>/research/raw/<slug>.md        — body
  (manifest.json is written by the caller in graphs/knowledge/distiller.py
   using the ManifestEntry list returned here)

Reference: docs/KNOWLEDGE-DISTILLER-INGESTION-PIPELINE-PLAN.md §Step 2
"""
import asyncio
import logging
import os
import re
import time
from typing import Optional

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
_API_BASE = "https://api.github.com"
_RAW_BASE = "https://raw.githubusercontent.com"
_USER_AGENT = "COELHONexus-KD-TierGH/1.0"
_HTTP_TIMEOUT = 30.0
_MAX_CONCURRENT = 10          # polite concurrency for raw fetches
_MAX_BLOB_BYTES = 2_000_000   # 2 MB — docs beyond this are almost certainly not docs
_MIN_OK_RATIO = 0.5           # abort tier only if <50% of blobs succeeded

# Markdown extensions we consider documentation content
_MD_EXTS = (".md", ".mdx", ".markdown")

# Paths to skip — CI metadata, vendored deps, test fixtures, compiled assets,
# locale-specific translations (English is canonical for rerank; locale dirs
# can explode the blob count for projects like React, Vue docs).
_SKIP_PREFIXES = (
    ".github/",
    ".gitlab/",
    ".vscode/",
    ".idea/",
    ".circleci/",
    "node_modules/",
    "vendor/",
    "tests/",
    "test/",
    "__tests__/",
    "spec/",
    "specs/",
    "fixtures/",
    "dist/",
    "build/",
    "out/",
    "target/",
    ".next/",
    ".nuxt/",
    "coverage/",
    "benchmarks/",
)

# Path substring blocklist — catches nested occurrences (e.g. `docs/zh-CN/`,
# `packages/*/node_modules/`, etc.) the prefix check misses.
_SKIP_SUBSTRINGS = (
    "/node_modules/",
    "/vendor/",
    "/__tests__/",
    "/fixtures/",
)

# Single-letter / country-code locale dirs — keep only `en/` when present.
_LOCALE_DIR_RE = re.compile(
    r"(^|/)(?:de|es|fr|it|ja|ko|pt|pt-br|ru|tr|vi|zh|zh-cn|zh-tw|zh-hans|zh-hant|ar|hi|pl|nl|sv)(/|$)",
    re.IGNORECASE,
)


# =============================================================================
# GitHub Tree API — single call, recursive
# =============================================================================
class _GitHubError(Exception):
    """Wraps non-retryable GitHub API errors (404, 401, malformed response)."""


def _build_headers() -> dict[str, str]:
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": _USER_AGENT,
    }
    token = os.environ.get("GITHUB_TOKEN", "").strip()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


@retry(
    reraise = True,
    retry = retry_if_exception_type((httpx.TimeoutException, httpx.NetworkError)),
    stop = stop_after_attempt(3),
    wait = wait_exponential_jitter(initial = 1, max = 30),
)
async def _fetch_tree(
    client: httpx.AsyncClient,
    org: str,
    repo: str,
    ref: str) -> list[dict]:
    """
    Return the list of tree nodes for `{org}/{repo}` at `ref`, recursive.
    Raises _GitHubError on non-200 + non-retryable failures.
    """
    url = f"{_API_BASE}/repos/{org}/{repo}/git/trees/{ref}"
    resp = await client.get(
        url,
        params = {"recursive": "1"},
        headers = _build_headers(),
    )
    if resp.status_code == 404:
        raise _GitHubError(f"repo not found: {org}/{repo} @ {ref}")
    if resp.status_code in (401, 403):
        # 403 can mean rate-limited OR forbidden; log both
        raise _GitHubError(
            f"auth/rate-limit from GitHub API ({resp.status_code}): {resp.text[:160]}"
        )
    resp.raise_for_status()
    data = resp.json()
    tree = data.get("tree") or []
    if data.get("truncated"):
        logger.warning(
            f"[tier-gh] tree TRUNCATED for {org}/{repo}@{ref} ({len(tree)} nodes) — "
            f"very large repo, some markdown may be missed"
        )
    return tree


def _filter_md_paths(tree: list[dict]) -> list[str]:
    """
    Keep only blob nodes with a markdown extension, excluding vendored /
    test / locale / CI paths. Returns sorted list of repo-relative paths
    (e.g. `README.md`, `docs/quickstart.md`).
    """
    paths: list[str] = []
    for node in tree:
        if node.get("type") != "blob":
            continue
        path = (node.get("path") or "").strip()
        if not path:
            continue
        lowered = path.lower()
        if not lowered.endswith(_MD_EXTS):
            continue
        if any(lowered.startswith(p) for p in _SKIP_PREFIXES):
            continue
        if any(s in lowered for s in _SKIP_SUBSTRINGS):
            continue
        # Locale dirs — strip non-English translations.
        if _LOCALE_DIR_RE.search(lowered):
            continue
        # Size guard — GitHub tree node carries `size` for blobs.
        size = node.get("size")
        if isinstance(size, int) and size > _MAX_BLOB_BYTES:
            logger.info(f"[tier-gh] skip oversized blob: {path} ({size} bytes)")
            continue
        paths.append(path)
    # Stable output — README first (reading-order heuristic), then alphabetical.
    paths.sort(key = lambda p: (0 if p.lower().endswith("readme.md") and "/" not in p else 1, p))
    return paths


# =============================================================================
# Per-file raw fetch
# =============================================================================
@retry(
    reraise = True,
    retry = retry_if_exception_type((httpx.TimeoutException, httpx.NetworkError)),
    stop = stop_after_attempt(3),
    wait = wait_exponential_jitter(initial = 1, max = 10),
)
async def _fetch_raw_blob(
    client: httpx.AsyncClient,
    org: str,
    repo: str,
    branch: str,
    path: str) -> tuple[str, bytes]:
    """
    Fetch one markdown blob from raw.githubusercontent.com. Returns
    (url, body_bytes). Caller decodes and checks for emptiness.
    """
    url = f"{_RAW_BASE}/{org}/{repo}/{branch}/{path}"
    resp = await client.get(url, headers = {"User-Agent": _USER_AGENT})
    resp.raise_for_status()
    return url, resp.content


def _derive_slug_from_path(path: str, org: str, repo: str) -> str:
    """
    Produce a stable, filesystem-safe slug for a repo-relative path.
    Example: 'docs/guides/quickstart.md' → 'github-{org}-{repo}-docs-guides-quickstart'
    """
    stem = path
    for ext in _MD_EXTS:
        if stem.lower().endswith(ext):
            stem = stem[: -len(ext)]
            break
    raw = f"github-{org}-{repo}-{stem}".lower()
    slug = re.sub(r"[^a-z0-9]+", "-", raw).strip("-")
    return slug[:120] or f"github-{org}-{repo}-index"


# =============================================================================
# Public entry point
# =============================================================================
async def ingest_github_tree(
    cfg: DocsIngestionConfig,
    storage: MinIOStudyStorage) -> IngestResult:
    """
    Tier-GH ingestion. Called by the dispatcher in services.knowledge.ingestion
    when `cfg.github_discover == "readme_only"`.

    Requires `cfg.github_org`, `cfg.github_repo`, `cfg.github_default_branch`.
    Falls back with an empty-result RuntimeError if any are missing — the
    dispatcher should have pre-validated; this is a defensive check.
    """
    org = cfg.github_org
    repo = cfg.github_repo
    branch = cfg.github_default_branch
    if not (org and repo and branch):
        raise RuntimeError(
            f"Tier-GH called without required GitHub metadata: "
            f"org={org!r} repo={repo!r} branch={branch!r}. "
            f"The router should have supplied these from the resolver's "
            f"`source_signals` payload."
        )

    logger.info(
        f"[tier-gh] start framework={cfg.framework!r} repo={org}/{repo}@{branch}"
    )

    # -----------------------------------------------------------------
    # Step 1 — Tree API (one call)
    # -----------------------------------------------------------------
    async with httpx.AsyncClient(
        timeout = httpx.Timeout(_HTTP_TIMEOUT, connect = 10.0),
        follow_redirects = True,
    ) as client:
        try:
            tree = await _fetch_tree(client, org, repo, branch)
        except _GitHubError as e:
            raise RuntimeError(f"Tier-GH tree fetch failed: {e}") from e

        md_paths = _filter_md_paths(tree)
        logger.info(
            f"[tier-gh] {org}/{repo}: {len(md_paths)} markdown files "
            f"(of {len(tree)} total tree nodes)"
        )
        if not md_paths:
            raise RuntimeError(
                f"Tier-GH found zero markdown files in {org}/{repo}@{branch}. "
                f"Repo may have no README / docs/. Dispatcher should fall back "
                f"to Tier 4 Playwright for a last-resort attempt."
            )

        # -----------------------------------------------------------------
        # Step 2 — parallel raw fetches with a semaphore cap
        # -----------------------------------------------------------------
        progress = IngestProgress(cfg.study_id)
        await progress.start(tier = "github_readme_only", total = len(md_paths))

        sem = asyncio.Semaphore(_MAX_CONCURRENT)
        failures: list[tuple[str, str]] = []  # [(path, reason), ...]
        manifest: list[ManifestEntry] = []
        total_bytes = 0
        # Shared counter — atomic enough for single-threaded asyncio.
        completed = 0

        async def _one(path: str) -> None:
            nonlocal total_bytes, completed
            async with sem:
                t0 = time.monotonic()
                try:
                    url, body = await _fetch_raw_blob(client, org, repo, branch, path)
                except httpx.HTTPStatusError as e:
                    err = f"HTTP {e.response.status_code}"
                    failures.append((path, err))
                    completed += 1
                    await progress.update(completed, f"(failed) {path}")
                    await progress.record_url(
                        path, status="http_error", tier="github_readme_only",
                        http_code=e.response.status_code,
                        fetch_ms=int((time.monotonic() - t0) * 1000),
                        error_msg=err,
                    )
                    return
                except Exception as e:
                    err = f"{type(e).__name__}: {e}"
                    failures.append((path, err))
                    completed += 1
                    await progress.update(completed, f"(failed) {path}")
                    await progress.record_url(
                        path, status="fetch_error", tier="github_readme_only",
                        fetch_ms=int((time.monotonic() - t0) * 1000),
                        error_msg=err,
                    )
                    return
                fetch_ms = int((time.monotonic() - t0) * 1000)
                # Decode (best-effort UTF-8 with replacement — rare repos use
                # legacy encodings but replacement won't lose docs content).
                try:
                    content = body.decode("utf-8")
                except UnicodeDecodeError:
                    content = body.decode("utf-8", errors = "replace")
                slug = _derive_slug_from_path(path, org, repo)
                entry = await _write_raw(
                    storage = storage,
                    study_root = cfg.study_root,
                    slug = slug,
                    content = content,
                    url = url,
                    tier = "github_readme_only",
                    cfg = cfg,          # enforces the empty-content gate
                )
                if entry is not None:
                    manifest.append(entry)
                    total_bytes += entry.bytes
                completed += 1
                await progress.update(completed, url)
                await progress.record_url(
                    url, status="success", tier="github_readme_only",
                    http_code=200, fetch_ms=fetch_ms,
                    bytes_fetched=len(body or b""), extracted_chars=len(content),
                )

        try:
            await asyncio.gather(*(_one(p) for p in md_paths))
        finally:
            await progress.finish(status = "done" if manifest else "failed")
            await progress.close()

    # -----------------------------------------------------------------
    # Step 3 — result + partial-failure check
    # -----------------------------------------------------------------
    attempted = len(md_paths)
    succeeded = len(manifest)
    fail_count = len(failures)

    if attempted > 0 and succeeded / attempted < _MIN_OK_RATIO:
        logger.error(
            f"[tier-gh] ABORT: only {succeeded}/{attempted} files succeeded "
            f"(below {_MIN_OK_RATIO*100:.0f}% threshold). Failures: "
            f"{failures[:5]}{'...' if fail_count > 5 else ''}"
        )
        raise RuntimeError(
            f"Tier-GH ingestion degraded beyond the acceptable threshold "
            f"({succeeded}/{attempted} files succeeded). "
            f"Caller should consider re-running via Tier 4 Playwright."
        )

    if fail_count:
        logger.warning(
            f"[tier-gh] {fail_count} files failed out of {attempted}. Samples: "
            f"{failures[:5]}"
        )

    logger.info(
        f"[tier-gh] OK — {succeeded} files, {total_bytes} bytes "
        f"({fail_count} failures)"
    )
    return IngestResult(
        tier_used = "github_readme_only",
        total_files = succeeded,
        total_bytes = total_bytes,
        manifest = manifest,
        skipped_urls = [f"{_RAW_BASE}/{org}/{repo}/{branch}/{p}" for p, _ in failures],
    )
