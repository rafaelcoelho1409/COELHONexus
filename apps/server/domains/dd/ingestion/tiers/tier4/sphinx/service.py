"""Sphinx discovery — I/O shell: objects.inv probing/fetch + DOM toctree
BFS discovery. Parsing/classification lives in domain.py."""
from __future__ import annotations
import domains
from . import domain, entities, params

import asyncio
import logging
from urllib.parse import urljoin, urlparse

import httpx



logger = logging.getLogger(__name__)

async def _probe_one(
    inv_url: str, client: httpx.AsyncClient,
) -> bytes | None:
    try:
        r = await client.get(
            inv_url, timeout=params.TIMEOUT_S, follow_redirects=True,
            headers={"User-Agent": params.SPHINX_USER_AGENT},
        )
    except Exception:
        return None
    if r.status_code != 200:
        return None
    if not r.content.startswith(params.V2_HEADER):
        return None
    return r.content


async def fetch_inventory(
    docs_root: str, *, client: httpx.AsyncClient,
) -> entities.Inventory | None:
    """Fetch+parse docs_root/objects.inv. For unversioned paths probes stable/latest/main siblings; returns None on 404/non-Sphinx/parse error."""
    if not docs_root.endswith("/"):
        docs_root = docs_root + "/"
    parsed = urlparse(docs_root)
    candidates: list[str] = [docs_root]
    if not domain.has_version_segment(parsed.path):
        for sib in params.INVENTORY_SIBLINGS:
            candidates.append(urljoin(docs_root, sib))

    for base in candidates:
        inv_url = urljoin(base, params.INVENTORY_FILENAME)
        raw = await _probe_one(inv_url, client)
        if raw is None:
            continue
        inv = domain.parse_inventory_v2(raw, base)
        if inv is None:
            continue
        logger.info(
            f"[objects.inv] {inv_url}: {inv.project} v{inv.version} — "
            f"{len(inv.entities)} entities, "
            f"{len(inv.doc_pages())} doc pages, "
            f"{len(inv.all_pages())} pages total"
        )
        return inv
    return None


async def discover_via_toctree(
    landing_url: str,
    *,
    host: str,
    subtree: str,
    client: httpx.AsyncClient,
    max_depth: int = params.TOCTREE_MAX_DEPTH,
    max_pages: int = params.TOCTREE_MAX_PAGES,
) -> list[str]:
    """BFS-expand sidebar+body links; skips BFS if landing sidebar has a full tree. Returns [] for non-Sphinx pages."""

    def _in_scope(u: str) -> bool:
        return domains.dd.ingestion.tiers.tier4.domain.crawlable_link(u, host, subtree)

    sem = asyncio.Semaphore(params.NAV_CONCURRENCY)

    async def _read(u: str) -> dict[str, list[str]]:
        async with sem:
            try:
                r = await client.get(
                    u, timeout=params.TIMEOUT_S, follow_redirects=True,
                    headers={"User-Agent": params.SPHINX_USER_AGENT},
                )
            except Exception:
                return {"sidebar": [], "body": []}
        if r.status_code != 200:
            return {"sidebar": [], "body": []}
        if "html" not in (r.headers.get("content-type") or "").lower():
            return {"sidebar": [], "body": []}
        return domain.extract_internal_pages(r.text or "", str(r.url))

    landing = await _read(landing_url)
    sidebar0 = landing["sidebar"]
    body0 = landing["body"]
    if not sidebar0 and not body0:
        return []  # not a Sphinx/MkDocs site

    # Insertion-order dedup — preserves author chapter order (sorting alphabetizes: Bash html_node regression).
    discovered: dict[str, None] = {landing_url: None}
    for src in (sidebar0, body0):
        for u in src:
            if u not in discovered and _in_scope(u):
                discovered[u] = None

    # Fast path: landing sidebar already lists a full tree.
    if len(sidebar0) >= params.FULL_TREE_HINT:
        logger.info(
            f"[sphinx-nav] {host}{subtree or ''}: {len(discovered)} URLs "
            f"(landing sidebar full tree, body added {len(body0)})"
        )
        return list(discovered.keys())

    # Expand both surfaces on each section page. max_pages caps extra HTTP fetches.
    frontier = [u for u in discovered if u != landing_url]
    fetched = 1
    body_recovered = 0
    depth = 1
    while frontier and depth <= max_depth and fetched < max_pages:
        budget = max(0, max_pages - fetched)
        batch = frontier[:budget]
        fetched += len(batch)
        results = await asyncio.gather(*(_read(u) for u in batch))
        new: list[str] = []
        for page in results:
            for link in page["sidebar"]:
                if link not in discovered and _in_scope(link):
                    discovered[link] = None
                    new.append(link)
            for link in page["body"]:
                if link not in discovered and _in_scope(link):
                    discovered[link] = None
                    new.append(link)
                    body_recovered += 1
        frontier = new
        depth += 1

    logger.info(
        f"[sphinx-nav] {host}{subtree or ''}: {len(discovered)} URLs "
        f"({fetched} pages read, depth≤{max_depth}, body recovered "
        f"{body_recovered} extras)"
    )
    return list(discovered.keys())
