"""Pure HTML→Markdown extraction (chrome strip + math normalization + convert).

Doc sites that pre-render math server-side (GitBook, mdBook,
sphinxcontrib-katex, MyST-Sphinx, MathJax-flavored Jupyter exports) ship
the same equation THREE times in the DOM: the accessible MathML, the
visual KaTeX/MathJax HTML, and the canonical LaTeX source. Without
pre-processing, markdownify converts each textual piece to prose,
producing the unreadable "triple-print mush" pattern.

Fix: walk every KaTeX `<annotation encoding="application/x-tex">` and
every MathJax `<mjx-container>` / `<script type="math/tex">`, replace the
OUTERMOST enclosing math-wrapper with a single text node carrying the
TeX source wrapped in `$..$` (inline) or `$$..$$` (display) markers.
markdownify then sees just text and passes it through; the client
renderer (content_renderer.js) picks up the delimiters and KaTeX
auto-render produces clean math. Idempotent — zero math containers → no
mutation.
"""
from __future__ import annotations

import logging
import re
from typing import Optional

from bs4 import BeautifulSoup, NavigableString, Tag
from markdownify import markdownify as _md

from .params import CHROME_SELECTORS, CONTENT_SELECTORS


logger = logging.getLogger(__name__)


def strip_chrome(soup: BeautifulSoup) -> None:
    for sel in CHROME_SELECTORS:
        for el in soup.select(sel):
            el.decompose()


def find_content_root(soup: BeautifulSoup):
    for sel in CONTENT_SELECTORS:
        node = soup.select_one(sel)
        if node and node.get_text(strip = True):
            return node
    return soup.body or soup


def _collapse_blank_lines(s: str) -> str:
    return re.sub(r"\n{3,}", "\n\n", s)


def _katex_container_and_display(annotation: Tag) -> tuple[Optional[Tag], bool]:
    """For a KaTeX ``<annotation encoding="application/x-tex">`` node,
    walk up to find the outermost wrapping span and report whether it's
    display math (``span.katex-display`` ancestor) or inline."""
    katex_span: Optional[Tag] = None
    is_display = False
    for anc in annotation.parents:
        classes = anc.get("class") or [] if isinstance(anc, Tag) else []
        if "katex-display" in classes:
            return anc, True
        if "katex" in classes and katex_span is None:
            katex_span = anc
            par = anc.parent
            if par is not None and isinstance(par, Tag) \
                    and "katex-display" in (par.get("class") or []):
                return par, True
    return katex_span, is_display


def _mathjax_source(container: Tag) -> Optional[str]:
    """Extract the TeX source from a MathJax container. Tries (1) inner
    ``annotation`` element (MathJax3 MathML fallback), then (2) inner
    ``<script type="math/tex">`` (legacy MathJax2 / MathJax3 with assistive
    MathML disabled). Returns ``None`` if the container has neither."""
    ann = container.find("annotation", attrs = {"encoding": "application/x-tex"})
    if ann is not None:
        src = ann.get_text("", strip = False)
        if src.strip():
            return src
    scr = container.find(
        "script",
        attrs = {"type": lambda v: bool(v) and "math/tex" in v},
    )
    if scr is not None:
        src = scr.get_text("", strip = False)
        if src.strip():
            return src
    return None


def _wrap_math(src: str, display: bool) -> str:
    """Format an extracted TeX source as markdown math. Display blocks
    get newline padding so they stand alone in the markdown stream;
    inline math gets single-space padding so adjacent words don't fuse."""
    src = (src or "").strip()
    if not src:
        return ""
    delim = "$$" if display else "$"
    return (
        f"\n\n{delim}{src}{delim}\n\n" if display
        else f" {delim}{src}{delim} "
    )


def _normalize_math_to_markdown(soup: BeautifulSoup) -> None:
    """Replace KaTeX + MathJax server-rendered math containers with
    ``$..$`` / ``$$..$$``-delimited TeX in-place. Idempotent."""
    for ann in list(soup.find_all(
        "annotation", attrs = {"encoding": "application/x-tex"},
    )):
        src = ann.get_text("", strip = False)
        if not (src or "").strip():
            continue
        outer, is_display = _katex_container_and_display(ann)
        target = outer if outer is not None else ann
        target.replace_with(NavigableString(_wrap_math(src, is_display)))

    for cont in list(soup.find_all("mjx-container")):
        if cont.parent is None:
            continue
        src = _mathjax_source(cont)
        if not src:
            continue
        is_display = (cont.get("display") in ("true", "block")) or (
            "MathJax_Display" in (cont.get("class") or [])
        )
        cont.replace_with(NavigableString(_wrap_math(src, is_display)))

    for scr in list(soup.find_all(
        "script",
        attrs = {"type": lambda v: bool(v) and "math/tex" in v},
    )):
        if scr.parent is None:
            continue
        src = scr.get_text("", strip = False)
        if not (src or "").strip():
            scr.decompose()
            continue
        is_display = "mode=display" in (scr.get("type") or "")
        sib = scr.previous_sibling
        while sib is not None:
            if isinstance(sib, Tag):
                cls = sib.get("class") or []
                if any(c.startswith("MathJax") for c in cls):
                    nxt = sib.previous_sibling
                    sib.decompose()
                    sib = nxt
                    continue
            break
        scr.replace_with(NavigableString(_wrap_math(src, is_display)))


def html_to_markdown(html: str, source_url: Optional[str] = None) -> str:
    """Convert HTML to markdown. Returns "" on empty/garbage input.

    Behavior: parse → strip chrome → pick best content root → convert.
    Markdownify settings: ATX headings (## style), fenced code blocks
    (triple-backtick), strip <a> empties.
    """
    if not html or not html.strip():
        return ""
    try:
        soup = BeautifulSoup(html, "lxml")
    except Exception as e:
        logger.info(f"[extract] lxml parse failed for {source_url}: {e}; "
                    f"falling back to html.parser")
        soup = BeautifulSoup(html, "html.parser")

    strip_chrome(soup)
    _normalize_math_to_markdown(soup)
    root = find_content_root(soup)

    md = _md(
        str(root),
        heading_style = "ATX",
        code_language = "",
        bullets = "*-+",
        strip = ["script", "style"],
    )
    return _collapse_blank_lines(md).strip()


def extract_title(html: str) -> str:
    """Best-effort page title from <title> or first h1. Empty string on
    failure."""
    if not html:
        return ""
    try:
        soup = BeautifulSoup(html, "lxml")
    except Exception:
        soup = BeautifulSoup(html, "html.parser")
    if soup.title and soup.title.string:
        return soup.title.string.strip()[:200]
    h1 = soup.find("h1")
    if h1:
        return h1.get_text(strip = True)[:200]
    return ""
