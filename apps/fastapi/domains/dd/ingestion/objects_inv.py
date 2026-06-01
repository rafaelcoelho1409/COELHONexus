"""Sphinx ``objects.inv`` canonical inventory — the deterministic SOTA
layer for Sphinx/readthedocs.io page discovery.

Every Sphinx HTML build ships ``objects.inv`` at the docs root (since
v1.0b1, 2010). It's the source-of-truth Sphinx uses for intersphinx
cross-references — listing every toctree-reachable page (``std:doc``),
every documented Python object (``py:class``, ``py:function``,
``py:method``, …), and every explicitly-labelled section
(``std:label``), each with a stable URI and anchor. Format is frozen v2
(4-line ASCII header + zlib-compressed payload); v3 is GitHub-discussion
stage only with explicit non-breaking commitment.

This module sits as the **L2 layer** of the SOTA discovery cascade in
Tier 4: probed BEFORE the DOM-based ``sphinx_nav`` extractor. When it
exists, it provides:

  (a) **Deterministic page discovery** — every toctree-reachable page,
      no heuristic BFS. Recovers pages the sidebar collapses (e.g. ADTK
      ``notebooks/quickstart.html`` which the DOM extractor missed).
  (b) **Deterministic per-entity anchors** — each class / function /
      module has a known anchor ID. ``page_split`` uses these directly
      instead of heuristic thresholds, giving canonical per-entity
      virtual sub-pages.
  (c) **Coverage oracle** — `inventory.doc_pages() - discovered` is a
      hard signal of what we missed (typically only ``:orphan:`` pages,
      which are invisible to any approach).

Parsed in-house (no ``sphobjinv`` dependency) because the v2 format is
trivial and frozen. The 4-line header carries Project / Version
metadata; the zlib payload is one entry per line:

    name domain:role priority uri dispname

with ``$`` shorthand for ``name`` in URI and ``-`` shorthand for
``name`` in dispname. URIs are relative to the inventory's location.
"""
import logging
import re
import zlib
from typing import NamedTuple, Optional
from urllib.parse import urljoin, urlparse

import httpx


logger = logging.getLogger(__name__)


_TIMEOUT_S = 30.0
_USER_AGENT = "COELHONexus-DocsDistiller-Tier4/1.0"

# Inventory roles considered "narrative pages" for crawl discovery.
# ``std:doc`` = a Sphinx document (toctree-reachable). ``std:label`` =
# named cross-reference target; we keep these too because they often
# point at section anchors on otherwise-narrative pages.
_DOC_ROLES = frozenset({"std:doc", "std:label"})

# Inventory roles considered "splittable top-level entities" — each
# becomes one virtual sub-page when its page is fetched.
_SPLIT_TOP_ROLES = frozenset({
    "py:class", "py:exception", "py:function", "py:module",
    "cpp:class", "cpp:function", "cpp:struct",
    "js:class", "js:function",
})

# Member-level roles for the "1 huge class with N methods" fallback —
# only used when a page has <4 top-level entities but ≥4 members.
_SPLIT_MEMBER_ROLES = frozenset({
    "py:method", "py:attribute", "py:classmethod",
    "py:staticmethod", "py:property", "py:data",
    "cpp:function", "cpp:member",
    "js:function", "js:attribute",
})

# Version-segment heuristic. RTD subtrees like ``/en/stable/`` already
# include the version, so a direct ``/en/stable/objects.inv`` probe
# works. Bare ``/en/`` (no version) probes multiple candidates.
_VERSION_RE = re.compile(r"^(?:stable|latest|main|master|dev|v?\d.*)$")

# Stable v2 inventory header marker.
_V2_HEADER = b"# Sphinx inventory version 2"
_HEADER_LINES = 4


class InventoryEntity(NamedTuple):
    name: str        # "adtk.detector.ThresholdAD"
    role: str        # "py:class" / "std:doc" / "py:method" / …
    page_url: str    # absolute URL, no fragment
    anchor: str      # fragment ID, e.g. "adtk.detector.ThresholdAD"; "" for std:doc
    dispname: str    # human-readable display name


class Inventory(NamedTuple):
    project: str
    version: str
    base_url: str
    entities: list[InventoryEntity]

    def doc_pages(self) -> set[str]:
        """All distinct page URLs reachable from std:doc / std:label
        entries — the canonical crawl set."""
        return {
            e.page_url for e in self.entities
            if e.role in _DOC_ROLES and e.page_url
        }

    def all_pages(self) -> set[str]:
        """Every page URL referenced by ANY entity — superset of
        ``doc_pages`` plus any page hosting autodoc symbols."""
        return {e.page_url for e in self.entities if e.page_url}

    def splittable_entities_on(
        self, page_url: str,
    ) -> tuple[list[InventoryEntity], list[InventoryEntity]]:
        """Return (top_level, members) for a page. Top-level = classes,
        functions, exceptions, modules. Members = methods, attributes,
        etc. Caller chooses top first; falls back to members when the
        page is a single class with many methods."""
        norm = page_url.split("#", 1)[0]
        top: list[InventoryEntity] = []
        members: list[InventoryEntity] = []
        for e in self.entities:
            if e.page_url != norm or not e.anchor:
                continue
            if e.role in _SPLIT_TOP_ROLES:
                top.append(e)
            elif e.role in _SPLIT_MEMBER_ROLES:
                members.append(e)
        return top, members


def _has_version_segment(path: str) -> bool:
    """True when the path includes a recognizable version dir (stable,
    latest, vN.M, etc.) so we don't probe sibling versions."""
    for seg in (path or "").strip("/").split("/"):
        if _VERSION_RE.match(seg):
            return True
    return False


def _parse_inventory_v2(
    raw: bytes, base_url: str,
) -> Optional[Inventory]:
    """Parse the v2 binary format. Returns None on malformed input."""
    if not raw.startswith(_V2_HEADER):
        return None
    project = ""
    version = ""
    cursor = 0
    for _ in range(_HEADER_LINES):
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
        # ``$`` shorthand: when URI ends with ``#$`` use ``name`` as
        # the anchor (Sphinx's compact encoding for autodoc entries).
        if "$" in uri:
            uri = uri.replace("$", name)
        if dispname == "-":
            dispname = name
        # Reject externally-scoped entries (forward intersphinx refs).
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
            inv_url, timeout=_TIMEOUT_S, follow_redirects=True,
            headers={"User-Agent": _USER_AGENT},
        )
    except Exception:
        return None
    if r.status_code != 200:
        return None
    if not r.content.startswith(_V2_HEADER):
        return None
    return r.content


async def fetch_inventory(
    docs_root: str, *, client: httpx.AsyncClient,
) -> Optional[Inventory]:
    """Fetch+parse ``{docs_root}/objects.inv``. ``None`` on 404 / non-Sphinx /
    parse error → caller falls back to DOM-based discovery.

    For RTD-style URLs that lack a version segment (e.g. ``/en/`` rather
    than ``/en/stable/``), we probe a small set of version siblings
    (``stable/``, ``latest/``, ``main/``) and use the first one that
    returns a v2 inventory. Each candidate's URL becomes the inventory's
    ``base_url`` so relative URIs resolve to the right version.
    """
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
