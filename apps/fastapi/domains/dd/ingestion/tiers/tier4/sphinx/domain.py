"""Sphinx discovery — pure helpers (no I/O): objects.inv v2 binary parsing,
sidebar/body link extraction from an already-fetched HTML string, and
multi-topic page splitting (inventory/autodoc/anchor precedence). Network
fetching lives in service.py."""
from __future__ import annotations
import domains
from . import entities, params, patterns

import logging
import zlib
from typing import Optional
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup, Tag



logger = logging.getLogger(__name__)


def has_version_segment(path: str) -> bool:
    """Path includes a version dir (stable/latest/vN.M) → skip sibling probe."""
    for seg in (path or "").strip("/").split("/"):
        if patterns.VERSION_RE.match(seg):
            return True
    return False


def parse_inventory_v2(
    raw: bytes, base_url: str,
) -> Optional[entities.Inventory]:
    """Parse v2 binary. None on malformed input."""
    if not raw.startswith(params.V2_HEADER):
        return None
    project = ""
    version = ""
    cursor = 0
    for _ in range(params.HEADER_LINES):
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

    parsed: list[entities.InventoryEntity] = []
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
        parsed.append(entities.InventoryEntity(
            name=name, role=role, page_url=page_url,
            anchor=anchor, dispname=dispname,
        ))
    return entities.Inventory(
        project=project, version=version,
        base_url=base_url, entities=parsed,
    )


def _normalize_href(href: str, base_url: str) -> str | None:
    href = (href or "").strip()
    if not href or href.startswith(params.SKIP_HREF_PREFIXES):
        return None
    full = urljoin(base_url, href).split("#", 1)[0]
    if not full.startswith(("http://", "https://")):
        return None
    path = urlparse(full).path or ""
    if patterns.EXCLUDE_PATH_RE.search(path) or patterns.EXCLUDE_EXT_RE.search(path):
        return None
    return full


def _sidebar_links(soup: BeautifulSoup, base_url: str) -> list[str]:
    out: list[str] = []
    seen: set[str] = set()
    for sel in params.SIDEBAR_SELECTORS:
        anchors = soup.select(sel)
        if not anchors:
            continue
        for a in anchors:
            full = _normalize_href(a.get("href"), base_url)
            if full and full not in seen:
                seen.add(full)
                out.append(full)
        break  # first-matching theme wins
    return out


def _body_links(soup: BeautifulSoup, base_url: str) -> list[str]:
    root = None
    for sel in params.ARTICLE_ROOTS:
        node = soup.select_one(sel)
        if node:
            root = node
            break
    if root is None:
        root = soup.body or soup
    out: list[str] = []
    seen: set[str] = set()
    for sel in params.BODY_SELECTORS:
        for a in root.select(sel):
            full = _normalize_href(a.get("href"), base_url)
            if full and full not in seen:
                seen.add(full)
                out.append(full)
    return out


def extract_internal_pages(html: str, base_url: str) -> dict[str, list[str]]:
    """Sphinx/MkDocs surfaces → {sidebar, body}. Both empty ⇒ not Sphinx/MkDocs."""
    if not html:
        return {"sidebar": [], "body": []}
    try:
        soup = BeautifulSoup(html, "lxml")
    except Exception:
        soup = BeautifulSoup(html, "html.parser")
    return {
        "sidebar": _sidebar_links(soup, base_url),
        "body": _body_links(soup, base_url),
    }


def extract_sidebar_links(html: str, base_url: str) -> list[str]:
    """Sidebar-only accessor (kept for fixture tests)."""
    return extract_internal_pages(html, base_url)["sidebar"]


def _slugify(s: str) -> str:
    return patterns.SLUG_RE.sub("-", (s or "").lower()).strip("-")[:80] or "section"


def maybe_split_page(
    html: str, source_url: str, parent_title: str = "",
    inventory: Optional[entities.Inventory] = None,
) -> list[entities.SubPage]:
    """Split precedence: inventory (per-entity) → autodoc (≥4 dl.py.class or 1-class-N-methods) → anchor (≥12 H2). Returns [] if page stays whole."""
    if not html:
        return []
    try:
        soup = BeautifulSoup(html, "lxml")
    except Exception:
        soup = BeautifulSoup(html, "html.parser")

    domains.dd.ingestion.tiers.extract.domain.strip_chrome(soup)
    root = domains.dd.ingestion.tiers.extract.domain.find_content_root(soup)

    if inventory is not None:
        out = _split_by_inventory(soup, root, source_url, parent_title, inventory)
        if out:
            logger.info(
                f"[page-split] inventory split: {len(out)} virtual pages "
                f"from {source_url}"
            )
            return out

    out = _split_autodoc(root, source_url, parent_title)
    if out:
        logger.info(
            f"[page-split] autodoc split: {len(out)} virtual pages from "
            f"{source_url}"
        )
        return out

    out = _split_anchored(root, source_url, parent_title)
    if out:
        logger.info(
            f"[page-split] anchor split: {len(out)} virtual pages from "
            f"{source_url}"
        )
    return out


def _container_for_anchor(soup: BeautifulSoup, root: Tag, anchor: str) -> Optional[Tag]:
    """Resolve anchor id to container: autodoc → <dt>'s parent <dl>; narrative → <section>/<div.section>; older Sphinx → <h2>."""
    node = root.find(id=anchor) or soup.find(id=anchor)
    if node is None or not isinstance(node, Tag):
        return None
    if node.name == "dt":
        # Wrap up: the <dt> is part of a <dl> definition list.
        return node.parent if node.parent is not None else node
    if node.name in ("section",):
        return node
    if node.name == "div" and "section" in (node.get("class") or []):
        return node
    if node.name in ("h2", "h3"):
        sect = _h2_section_ancestor(node)
        return sect or node
    return node


def _split_by_inventory(
    soup: BeautifulSoup, root: Tag, source_url: str,
    parent_title: str, inventory: entities.Inventory,
) -> list[entities.SubPage]:
    """Inventory-driven split: one SubPage per splittable entity on page.
    Top-level first; falls back to members on 1-class-N-methods pages."""
    top, members = inventory.splittable_entities_on(source_url)
    chosen = top if len(top) >= params.AUTODOC_MIN_BLOCKS else (
        members if len(members) >= params.AUTODOC_MIN_BLOCKS else []
    )
    if not chosen:
        return []

    out: list[entities.SubPage] = []
    parent_url = source_url.split("#", 1)[0]
    seen: set[str] = set()
    n_stubs_dropped = 0
    for ent in chosen:
        if not ent.anchor or ent.anchor in seen:
            continue
        container = _container_for_anchor(soup, root, ent.anchor)
        if container is None:
            continue
        body_md = domains.dd.ingestion.tiers.extract.domain.html_to_markdown(str(container), source_url=source_url)
        if len(body_md.encode("utf-8")) < params.INVENTORY_MIN_BODY_BYTES:
            n_stubs_dropped += 1
            continue
        seen.add(ent.anchor)
        title_text = (ent.dispname or ent.name).rstrip("¶").strip()[:160]
        title = (
            f"{parent_title} — {title_text}"
            if parent_title and title_text and title_text not in parent_title
            else (title_text or parent_title or ent.anchor)
        )
        out.append(entities.SubPage(
            slug_suffix=_slugify(ent.anchor),
            sub_url=f"{parent_url}#{ent.anchor}",
            title=title,
            body_md=body_md,
        ))
    # Quality gate: if most entities were 1-line stubs (CPython errno consts etc.),
    # the parent IS the better corpus chunk than a handful of disconnected fragments.
    if len(out) < params.INVENTORY_MIN_SPLITS:
        if out or n_stubs_dropped:
            logger.info(
                f"[page-split] inventory split abandoned for {source_url}: "
                f"only {len(out)} useful sub-page(s) survived "
                f"(dropped {n_stubs_dropped} stub(s) <{params.INVENTORY_MIN_BODY_BYTES}B); "
                f"keeping parent page whole"
            )
        return []
    return out


def _split_autodoc(
    root: Tag, source_url: str, parent_title: str,
) -> list[entities.SubPage]:
    blocks = root.select(params.AUTODOC_SELECTOR)
    # Top-level only — a class with nested methods is ONE unit, not 1+N.
    blocks_set = set(id(b) for b in blocks)
    top: list[Tag] = []
    for b in blocks:
        if not any(id(a) in blocks_set for a in b.parents):
            top.append(b)
    # Fallback: 1-class-N-methods pages (ElasticSearch-Py indices.html) — split by members.
    if len(top) < params.AUTODOC_MIN_BLOCKS:
        member_sel = (
            "dl.py.method, dl.py.attribute, dl.py.classmethod, "
            "dl.py.staticmethod, dl.py.property, dl.py.data, "
            "dl.cpp.function, dl.cpp.member, dl.js.function, dl.js.attribute, "
            # Older Sphinx pre-namespaced forms
            "dl.method, dl.attribute, dl.classmethod, dl.staticmethod"
        )
        members = root.select(member_sel)
        if len(members) >= params.AUTODOC_MIN_BLOCKS:
            top = members
        else:
            return []

    out: list[entities.SubPage] = []
    parent_url = source_url.split("#", 1)[0]
    seen_ids: set[str] = set()
    for dl in top:
        # The id lives on the first <dt> child (Sphinx autodoc convention).
        dt = dl.find("dt", recursive=False) or dl.find("dt")
        if dt is None:
            continue
        anchor_id = (dt.get("id") or "").strip()
        if not anchor_id or anchor_id in seen_ids:
            continue
        seen_ids.add(anchor_id)
        title_text = dt.get_text(strip=True).rstrip("¶").strip()[:160]
        body_md = domains.dd.ingestion.tiers.extract.domain.html_to_markdown(str(dl), source_url=source_url)
        if len(body_md.encode("utf-8")) < params.MIN_BODY_BYTES:
            continue
        slug_suffix = _slugify(anchor_id) or _slugify(title_text)
        title = (
            f"{parent_title} — {title_text}"
            if parent_title and title_text not in parent_title
            else (title_text or parent_title or anchor_id)
        )
        out.append(entities.SubPage(
            slug_suffix=slug_suffix,
            sub_url=f"{parent_url}#{anchor_id}",
            title=title,
            body_md=body_md,
        ))
    return out


def _h2_section_ancestor(h2: Tag) -> Tag | None:
    """Enclosing section across Sphinx versions: 4+ emits `<section id=...>`;
    1-3 + nbsphinx use `<div class="section">`. Closest match wins."""
    for anc in h2.parents:
        if not isinstance(anc, Tag):
            continue
        if anc.name == "section" and anc.get("id"):
            return anc
        if anc.name == "div" and "section" in (anc.get("class") or []) \
                and anc.get("id"):
            return anc
    return None


def _section_html_for_h2(h2: Tag, anchor_id: str) -> str:
    """HTML for the section under h2. Prefers the enclosing section container
    when its id matches; falls back to h2 + siblings up to the next h2."""
    sect = _h2_section_ancestor(h2)
    if sect is not None and (sect.get("id") or "").strip() == anchor_id:
        return str(sect)
    parts = [str(h2)]
    for sib in h2.next_siblings:
        if isinstance(sib, Tag) and sib.name == "h2":
            break
        parts.append(str(sib))
    return "".join(parts)


def _split_anchored(
    root: Tag, source_url: str, parent_title: str,
) -> list[entities.SubPage]:
    h2s = root.select("h2")
    anchored: list[tuple[Tag, str]] = []
    for h in h2s:
        # `¶` permalink = author-intended discrete section.
        if h.select_one("a.headerlink") is None:
            continue
        anchor_id = (h.get("id") or "").strip()
        if not anchor_id:
            sect = _h2_section_ancestor(h)
            if sect is not None:
                anchor_id = (sect.get("id") or "").strip()
        if anchor_id:
            anchored.append((h, anchor_id))

    if len(anchored) < params.ANCHOR_MIN_H2:
        return []

    out: list[entities.SubPage] = []
    parent_url = source_url.split("#", 1)[0]
    seen_ids: set[str] = set()
    for h2, anchor_id in anchored:
        if anchor_id in seen_ids:
            continue
        seen_ids.add(anchor_id)
        sub_html = _section_html_for_h2(h2, anchor_id)
        body_md = domains.dd.ingestion.tiers.extract.domain.html_to_markdown(sub_html, source_url=source_url)
        if len(body_md.encode("utf-8")) < params.MIN_BODY_BYTES:
            continue
        title_text = h2.get_text(strip=True).rstrip("¶").strip()[:160]
        title = (
            f"{parent_title} — {title_text}"
            if parent_title and title_text and title_text not in parent_title
            else (title_text or parent_title or anchor_id)
        )
        out.append(entities.SubPage(
            slug_suffix=_slugify(anchor_id),
            sub_url=f"{parent_url}#{anchor_id}",
            title=title,
            body_md=body_md,
        ))
    return out
