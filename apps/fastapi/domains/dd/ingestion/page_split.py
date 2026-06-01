"""Split Sphinx pages that bundle many discrete sections into virtual sub-pages.

Some doc authors pack N independent items into a single HTML page —
either as anchor-permalink H2 sections (ADTK's ``notebooks/demo.html`` =
33 detector/transformer examples; Optuna ``faq.html`` = 24 Q&As) or as
Sphinx autodoc class/function blocks (PyTorch Geometric ``utils.html`` =
780KB of API ref; ElasticSearch-Py ``indices.html``, ``elasticsearch.html``;
Novu ``dto.html``). Both patterns defeat the digest stage: the planner
sees ONE source where the author meant N.

This module post-processes such pages: when the structural signature
matches, it returns a list of virtual sub-pages (one per anchor section
or per autodoc class/function), each carrying a synthetic slug-suffix,
the parent URL + ``#anchor`` for citation traceability, the section
title, and the markdown body of just that subtree. The caller writes
each sub-page as its own document so the digest treats it as a distinct
source. Pages that don't match either pattern return ``[]`` — caller
falls back to single-page storage unchanged.

Detection thresholds are deliberately conservative — only split when the
page is clearly multi-topic (≥12 anchored H2s or ≥4 autodoc blocks), so
ordinary tutorials and small API pages aren't fragmented.
"""
import logging
import re
from typing import NamedTuple, Optional, TYPE_CHECKING

from bs4 import BeautifulSoup, Tag

from .extract import _find_content_root, _strip_chrome, html_to_markdown


if TYPE_CHECKING:
    from .objects_inv import Inventory


logger = logging.getLogger(__name__)


# Minimum number of anchored H2 sections to trigger anchor-split. 12
# leaves typical tutorials (5-10 H2) alone but catches ADTK demo.html
# (33 H2s) and Optuna faq.html (24 H2s).
_ANCHOR_MIN_H2 = 12

# Minimum number of py-class/py-function blocks to trigger autodoc-split.
# PyG utils.html has dozens; ElasticSearch-Py indices.html has ~30. A
# small per-class page (<4 blocks) doesn't need fragmenting.
_AUTODOC_MIN_BLOCKS = 4

# Per-section minimum size after markdown conversion. Below this we drop
# the section instead of emitting a near-empty virtual page (matches the
# Tier 4 ``_MIN_OK_BYTES = 200``).
_MIN_BODY_BYTES = 200

# Sphinx autodoc declaration containers across Sphinx generations and
# languages. ``dl.py.class`` is modern (Sphinx 2+ `domain.objtype`
# convention); ``dl.class`` is the pre-namespaced form still emitted by
# older RTD-hosted projects (ADTK 0.6.2's ``api/detectors.html`` uses 15
# ``dl.class`` blocks and 117 ``dl.method`` blocks — the modern selector
# misses these entirely). Splitting at the class/function/module level
# is deliberate — nested ``dl.py.method`` blocks stay attached to their
# parent class for context (the inner-member fallback below splits
# 1-class-N-methods pages instead).
_AUTODOC_SELECTOR = (
    # Modern Sphinx 2+ (.py / .cpp / .js prefixed)
    "dl.py.class, dl.py.function, dl.py.exception, dl.py.module, "
    "dl.py.data, dl.py.attribute, dl.py.classmethod, dl.py.staticmethod, "
    "dl.cpp.class, dl.cpp.function, dl.cpp.struct, "
    "dl.js.function, dl.js.class, "
    # Older Sphinx + nbsphinx (pre-namespaced)
    "dl.class, dl.function, dl.exception, dl.data"
)

_SLUG_RE = re.compile(r"[^a-z0-9]+")


class SubPage(NamedTuple):
    slug_suffix: str   # appended to parent slug; never contains slashes
    sub_url: str       # parent URL + ``#anchor`` for citation traceability
    title: str         # human-readable section title
    body_md: str       # markdown for THIS sub-section only


def _slugify(s: str) -> str:
    return _SLUG_RE.sub("-", (s or "").lower()).strip("-")[:80] or "section"


def maybe_split_page(
    html: str, source_url: str, parent_title: str = "",
    inventory: Optional["Inventory"] = None,
) -> list[SubPage]:
    """Return virtual sub-pages if the page matches a split pattern,
    or ``[]`` to leave the page intact for single-doc storage.

    Detection precedence:
      1. **Inventory-driven** (when ``inventory`` is given): use Sphinx's
         own ``objects.inv`` entities for the page → deterministic,
         per-entity split. Replaces heuristic thresholds. Falls through
         if the page has no inventory entities OR they yield no usable
         sub-pages.
      2. **Autodoc heuristic**: ≥4 top-level ``dl.py.class``/etc. blocks,
         or ≥4 inner ``dl.py.method`` members on a 1-class page.
      3. **Anchor heuristic**: ≥12 H2 sections with ``a.headerlink``
         permalinks (the ``¶`` signature) — narrative cookbooks like
         ADTK ``demo.html`` or Optuna ``faq.html``.
    """
    if not html:
        return []
    try:
        soup = BeautifulSoup(html, "lxml")
    except Exception:
        soup = BeautifulSoup(html, "html.parser")

    _strip_chrome(soup)
    root = _find_content_root(soup)

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
    """Find the right HTML container for a given inventory anchor.

    The id usually lives on a Sphinx autodoc ``<dt>`` (so we want the
    enclosing ``<dl>``), but may also live on a ``<section>`` /
    ``<div class="section">`` (narrative pages) or directly on an
    ``<h2>``/``<h3>`` (older Sphinx). We resolve to the most informative
    container in each case so the markdown extract is coherent."""
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
    """Inventory-driven deterministic split. For each splittable entity
    on this page (per ``objects.inv``), extract the HTML container
    rooted at that entity's anchor and emit a SubPage. Top-level
    entities first; falls back to members on 1-class-N-methods pages."""
    top, members = inventory.splittable_entities_on(source_url)
    chosen = top if len(top) >= _AUTODOC_MIN_BLOCKS else (
        members if len(members) >= _AUTODOC_MIN_BLOCKS else []
    )
    if not chosen:
        return []

    out: list[SubPage] = []
    parent_url = source_url.split("#", 1)[0]
    seen: set[str] = set()
    for ent in chosen:
        if not ent.anchor or ent.anchor in seen:
            continue
        container = _container_for_anchor(soup, root, ent.anchor)
        if container is None:
            continue
        body_md = html_to_markdown(str(container), source_url=source_url)
        if len(body_md.encode("utf-8")) < _MIN_BODY_BYTES:
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
    return out


def _split_autodoc(
    root: Tag, source_url: str, parent_title: str,
) -> list[SubPage]:
    blocks = root.select(_AUTODOC_SELECTOR)
    # Keep only TOP-LEVEL blocks — a class with nested methods becomes ONE
    # split unit, not 1 + N. A block is top-level here iff no ancestor is
    # itself a member of ``blocks``.
    blocks_set = set(id(b) for b in blocks)
    top: list[Tag] = []
    for b in blocks:
        if not any(id(a) in blocks_set for a in b.parents):
            top.append(b)
    # Fallback: pages that are ONE huge class with N methods (e.g.
    # ElasticSearch-Py ``indices.html``: 1 class, 71 methods) — split by
    # the inner members instead so each method becomes its own source.
    if len(top) < _AUTODOC_MIN_BLOCKS:
        member_sel = (
            "dl.py.method, dl.py.attribute, dl.py.classmethod, "
            "dl.py.staticmethod, dl.py.property, dl.py.data, "
            "dl.cpp.function, dl.cpp.member, dl.js.function, dl.js.attribute, "
            # Older Sphinx pre-namespaced forms
            "dl.method, dl.attribute, dl.classmethod, dl.staticmethod"
        )
        members = root.select(member_sel)
        if len(members) >= _AUTODOC_MIN_BLOCKS:
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
        if len(body_md.encode("utf-8")) < _MIN_BODY_BYTES:
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
    """Find the enclosing section container for an H2 across Sphinx versions.

    Sphinx 4+ emits ``<section id="...">``; Sphinx 1-3 and nbsphinx
    notebook output (ADTK ``demo.html``) emit ``<div class="section">``.
    Walk ancestors looking for either, preferring the closest match.
    """
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
    """Return HTML for the section rooted at ``h2``. Prefers the enclosing
    section container (Sphinx 4 ``<section>`` OR pre-4 / nbsphinx
    ``<div class="section">``) when its id matches; falls back to
    "h2 + following siblings until next h2" for handwritten markup."""
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
        # Sphinx ``¶`` permalink — the structural signature of an
        # author-intended discrete section vs an incidental sub-heading.
        # nbsphinx Jupyter notebooks (ADTK demo.html) use this too.
        if h.select_one("a.headerlink") is None:
            continue
        anchor_id = (h.get("id") or "").strip()
        if not anchor_id:
            # Sphinx 4+ uses <section id="...">; pre-4 and nbsphinx use
            # <div class="section" id="...">.
            sect = _h2_section_ancestor(h)
            if sect is not None:
                anchor_id = (sect.get("id") or "").strip()
        if anchor_id:
            anchored.append((h, anchor_id))

    if len(anchored) < _ANCHOR_MIN_H2:
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
        if len(body_md.encode("utf-8")) < _MIN_BODY_BYTES:
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
