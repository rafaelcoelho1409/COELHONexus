"""Sphinx `objects.inv` parser — deterministic page + entity discovery.

Probed before the DOM-based `sphinx_nav` extractor. When present, gives
canonical toctree-reachable page set + per-entity anchors (used by
`page_split` instead of heuristics). Format is frozen v2 (4-line ASCII
header + zlib payload, one `name domain:role priority uri dispname` per
line; `$` shorthand → name in URI, `-` → name in dispname).
"""
import logging
import zlib
from typing import Optional
from urllib.parse import urljoin, urlparse

import httpx

from .entities import Inventory, InventoryEntity
from .params import (
    HEADER_LINES,
    SPHINX_USER_AGENT,
    TIMEOUT_S,
    V2_HEADER,
)
from .patterns import VERSION_RE


logger = logging.getLogger(__name__)


def _has_version_segment(path: str) -> bool:
    """Path includes a version dir (stable/latest/vN.M) → skip sibling probe."""
    for seg in (path or "").strip("/").split("/"):
        if VERSION_RE.match(seg):
            return True
    return False


def _parse_inventory_v2(
    raw: bytes, base_url: str,
) -> Optional[Inventory]:
    """Parse v2 binary. None on malformed input."""
    if not raw.startswith(V2_HEADER):
        return None
    project = ""
    version = ""
    cursor = 0
    for _ in range(HEADER_LINES):
        nl = raw.find(b"\n", cursor)
        if nl == -1:
            return None
        line = raw[cursor:nl].decode("utf-8", "replace")
        cursor = nl + 1
        if line.startswith("# Project:"):
            project = line.split(":", 1)[1].strip()
        elif line.startswith("# Version:"):
            version = line.split(":", 1)[1].strip()
    try:
        payload = zlib.decompress(raw[cursor:]).decode("utf-8", "replace")
    except Exception as e:
        logger.info(f"[objects.inv] zlib decompress failed: {e}")
        return None

    entities: list[InventoryEntity] = []
    seen: set[tuple[str, str]] = set()
    for line in payload.splitlines():
        if not line or line.startswith("#"):
            continue
        # name domain:role priority uri dispname
        parts = line.split(None, 4)
        if len(parts) < 5:
            continue
        name, role, _priority, uri, dispname = parts
        # Sphinx `$` shorthand → use `name` as anchor.
        if "$" in uri:
            uri = uri.replace("$", name)
        if dispname == "-":
            dispname = name
        # Reject externally-scoped (forward intersphinx refs).
        if "://" in uri:
            continue
        full = urljoin(base_url, uri)
        page_url, _, anchor = full.partition("#")
        if not page_url:
            continue
        key = (page_url, anchor or name)
        if key in seen:
            continue
        seen.add(key)
        entities.append(InventoryEntity(
            name=name, role=role, page_url=page_url,
            anchor=anchor, dispname=dispname,
        ))
    return Inventory(
        project=project, version=version,
        base_url=base_url, entities=entities,
    )


async def _probe_one(
    inv_url: str, client: httpx.AsyncClient,
) -> Optional[bytes]:
    try:
        r = await client.get(
            inv_url, timeout=TIMEOUT_S, follow_redirects=True,
            headers={"User-Agent": SPHINX_USER_AGENT},
        )
    except Exception:
        return None
    if r.status_code != 200:
        return None
    if not r.content.startswith(V2_HEADER):
        return None
    return r.content


async def fetch_inventory(
    docs_root: str, *, client: httpx.AsyncClient,
) -> Optional[Inventory]:
    """Fetch+parse `{docs_root}/objects.inv`. None on 404 / non-Sphinx / parse error.

    For unversioned URLs (`/en/`) probes stable/latest/main siblings; the
    successful candidate's URL becomes the inventory's `base_url`."""
    if not docs_root.endswith("/"):
        docs_root = docs_root + "/"
    parsed = urlparse(docs_root)
    candidates: list[str] = [docs_root]
    if not _has_version_segment(parsed.path):
        for sib in ("stable/", "latest/", "main/"):
            candidates.append(urljoin(docs_root, sib))

    for base in candidates:
        inv_url = urljoin(base, "objects.inv")
        raw = await _probe_one(inv_url, client)
        if raw is None:
            continue
        inv = _parse_inventory_v2(raw, base)
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
