"""Split multi-topic Sphinx pages into virtual sub-pages.

Some docs pack N discrete items into one HTML page (anchored-H2 cookbooks
like ADTK demo.html, autodoc API refs like PyTorch Geometric utils.html).
Without splitting, digest sees ONE source where the author meant N. When
the structural signature matches (≥12 anchored H2s or ≥4 autodoc blocks),
returns SubPages each carrying parent_url#anchor for traceability. Returns
[] when neither pattern matches.
"""
import logging
from typing import Optional, TYPE_CHECKING

from bs4 import BeautifulSoup, Tag

from ...extract import find_content_root, html_to_markdown, strip_chrome
from .entities import SubPage
from .params import (
    ANCHOR_MIN_H2,
    AUTODOC_MIN_BLOCKS,
    AUTODOC_SELECTOR,
    INVENTORY_MIN_BODY_BYTES,
    INVENTORY_MIN_SPLITS,
    MIN_BODY_BYTES,
)
from .patterns import SLUG_RE


if TYPE_CHECKING:
    from .entities import Inventory


logger = logging.getLogger(__name__)


def _slugify(s: str) -> str:
    return SLUG_RE.sub("-", (s or "").lower()).strip("-")[:80] or "section"


def maybe_split_page(
    html: str, source_url: str, parent_title: str = "",
    inventory: Optional["Inventory"] = None,
) -> list[SubPage]:
    """Return virtual sub-pages or [] if the page should stay whole.

    Precedence: inventory (deterministic per-entity) → autodoc (≥4 dl.py.class
    or 1-class-with-≥4-methods) → anchor (≥12 H2 with `a.headerlink`).
    """
    if not html:
        return []
    try:
        soup = BeautifulSoup(html, "lxml")
    except Exception:
        soup = BeautifulSoup(html, "html.parser")

    strip_chrome(soup)
    root = find_content_root(soup)

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
    """Resolve an inventory anchor id to its enclosing container.

    Sphinx autodoc puts the id on `<dt>` (→ enclosing `<dl>`); narrative pages
    use `<section>` or `<div class="section">`; older Sphinx puts it on `<h2>`."""
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
    parent_title: str, inventory: "Inventory",
) -> list[SubPage]:
    """Inventory-driven split: one SubPage per splittable entity on page.
    Top-level first; falls back to members on 1-class-N-methods pages."""
    top, members = inventory.splittable_entities_on(source_url)
    chosen = top if len(top) >= AUTODOC_MIN_BLOCKS else (
        members if len(members) >= AUTODOC_MIN_BLOCKS else []
    )
    if not chosen:
        return []

    out: list[SubPage] = []
    parent_url = source_url.split("#", 1)[0]
    seen: set[str] = set()
    n_stubs_dropped = 0
    for ent in chosen:
        if not ent.anchor or ent.anchor in seen:
            continue
        container = _container_for_anchor(soup, root, ent.anchor)
        if container is None:
            continue
        body_md = html_to_markdown(str(container), source_url=source_url)
        if len(body_md.encode("utf-8")) < INVENTORY_MIN_BODY_BYTES:
            n_stubs_dropped += 1
            continue
        seen.add(ent.anchor)
        title_text = (ent.dispname or ent.name).rstrip("¶").strip()[:160]
        title = (
            f"{parent_title} — {title_text}"
            if parent_title and title_text and title_text not in parent_title
            else (title_text or parent_title or ent.anchor)
        )
        out.append(SubPage(
            slug_suffix=_slugify(ent.anchor),
            sub_url=f"{parent_url}#{ent.anchor}",
            title=title,
            body_md=body_md,
        ))
    # Quality gate: if most entities were 1-line stubs (CPython errno consts etc.),
    # the parent IS the better corpus chunk than a handful of disconnected fragments.
    if len(out) < INVENTORY_MIN_SPLITS:
        if out or n_stubs_dropped:
            logger.info(
                f"[page-split] inventory split abandoned for {source_url}: "
                f"only {len(out)} useful sub-page(s) survived "
                f"(dropped {n_stubs_dropped} stub(s) <{INVENTORY_MIN_BODY_BYTES}B); "
                f"keeping parent page whole"
            )
        return []
    return out


def _split_autodoc(
    root: Tag, source_url: str, parent_title: str,
) -> list[SubPage]:
    blocks = root.select(AUTODOC_SELECTOR)
    # Top-level only — a class with nested methods is ONE unit, not 1+N.
    blocks_set = set(id(b) for b in blocks)
    top: list[Tag] = []
    for b in blocks:
        if not any(id(a) in blocks_set for a in b.parents):
            top.append(b)
    # Fallback: 1-class-N-methods pages (ElasticSearch-Py indices.html) — split by members.
    if len(top) < AUTODOC_MIN_BLOCKS:
        member_sel = (
            "dl.py.method, dl.py.attribute, dl.py.classmethod, "
            "dl.py.staticmethod, dl.py.property, dl.py.data, "
            "dl.cpp.function, dl.cpp.member, dl.js.function, dl.js.attribute, "
            # Older Sphinx pre-namespaced forms
            "dl.method, dl.attribute, dl.classmethod, dl.staticmethod"
        )
        members = root.select(member_sel)
        if len(members) >= AUTODOC_MIN_BLOCKS:
            top = members
        else:
            return []

    out: list[SubPage] = []
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
        body_md = html_to_markdown(str(dl), source_url=source_url)
        if len(body_md.encode("utf-8")) < MIN_BODY_BYTES:
            continue
        slug_suffix = _slugify(anchor_id) or _slugify(title_text)
        title = (
            f"{parent_title} — {title_text}"
            if parent_title and title_text not in parent_title
            else (title_text or parent_title or anchor_id)
        )
        out.append(SubPage(
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
) -> list[SubPage]:
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

    if len(anchored) < ANCHOR_MIN_H2:
        return []

    out: list[SubPage] = []
    parent_url = source_url.split("#", 1)[0]
    seen_ids: set[str] = set()
    for h2, anchor_id in anchored:
        if anchor_id in seen_ids:
            continue
        seen_ids.add(anchor_id)
        sub_html = _section_html_for_h2(h2, anchor_id)
        body_md = html_to_markdown(sub_html, source_url=source_url)
        if len(body_md.encode("utf-8")) < MIN_BODY_BYTES:
            continue
        title_text = h2.get_text(strip=True).rstrip("¶").strip()[:160]
        title = (
            f"{parent_title} — {title_text}"
            if parent_title and title_text and title_text not in parent_title
            else (title_text or parent_title or anchor_id)
        )
        out.append(SubPage(
            slug_suffix=_slugify(anchor_id),
            sub_url=f"{parent_url}#{anchor_id}",
            title=title,
            body_md=body_md,
        ))
    return out
