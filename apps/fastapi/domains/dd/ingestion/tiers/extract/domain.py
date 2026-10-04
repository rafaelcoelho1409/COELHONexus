"""Pure HTML→Markdown: strip chrome, normalize KaTeX/MathJax triple-print math (MathML + visual + LaTeX all in DOM) to $…$/$$$…$$$ text nodes so markdownify sees one clean representation."""
from __future__ import annotations
from . import params

import ast
import logging
import re
from typing import Optional

from bs4 import BeautifulSoup, NavigableString, Tag
from markdownify import markdownify as _md



logger = logging.getLogger(__name__)


def strip_chrome(soup: BeautifulSoup) -> None:
    for sel in params.CHROME_SELECTORS:
        for el in soup.select(sel):
            el.decompose()


def find_content_root(soup: BeautifulSoup):
    for sel in params.CONTENT_SELECTORS:
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
    """Extract TeX from MathJax container: tries annotation (MathJax3 MathML), then script[type=math/tex] (MathJax2). None if neither present."""
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


_LANG_CLASS_RE = re.compile(
    r"^(?:language|lang|highlight-source|highlight)[-_]([A-Za-z][A-Za-z0-9+#_-]{0,19})$"
)
_LANG_IGNORE = frozenset({
    "none", "text", "plain", "plaintext", "nohighlight", "notranslate",
    "source", "highlight", "code", "output", "raw",
})
# Sphinx's `highlight-default` means "python3, falling back to no highlighting
# when it doesn't lex" — i.e. unlabeled Python-library examples (elasticsearch-py,
# requests). Sniffed from the content rather than assumed.
_DEFAULT_MARK = "@default"
_PY_STATEMENTS = (
    ast.Import, ast.ImportFrom, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef,
    ast.Assign, ast.AugAssign, ast.AnnAssign, ast.For, ast.AsyncFor, ast.While,
    ast.If, ast.With, ast.AsyncWith, ast.Try, ast.Return, ast.Raise,
)
_LANG_ALIASES = {"python3": "python", "py3": "python", "py": "python"}


def _lang_from_classes(classes) -> str:
    for c in classes or []:
        m = _LANG_CLASS_RE.match(c)
        if not m:
            continue
        lang = m.group(1).lower()
        if lang == "default":
            return _DEFAULT_MARK
        if lang in _LANG_IGNORE:
            continue
        return _LANG_ALIASES.get(lang, lang)
    return ""


def _sniff_default_language(text: str) -> str:
    """'pycon' for a >>> session, 'python' when the snippet parses AND has real statements or a call (a bare word, number or JSON literal also parses, and must not be called Python), else ''."""
    t = (text or "").strip()
    if not t:
        return ""
    if t.startswith(">>>"):
        return "pycon"
    try:
        tree = ast.parse(t)
    except (SyntaxError, ValueError, MemoryError, RecursionError):
        return ""
    for n in ast.walk(tree):
        if isinstance(n, _PY_STATEMENTS) or (isinstance(n, ast.Expr) and isinstance(n.value, (ast.Call, ast.Await))):
            return "python"
    return ""


def code_language(pre: Tag) -> str:
    """Language of a `<pre>` code block, "" when the markup doesn't say. markdownify emits a bare fence unless asked, so every HTML-converted tier (2/3/4) produced untagged code — asyncio's 73 code blocks all had none, though the Sphinx source marks them `highlight-python3`. Looks at the pre, its `<code>` child, a `data-language` attribute, then up to three ancestors (Sphinx wraps `<pre>` in `<div class="highlight-python3">`; MkDocs/Docusaurus/GitHub use `language-x` / `highlight-source-x`)."""
    nodes = [pre]
    code = pre.find("code")
    if code is not None:
        nodes.append(code)
    nodes.extend(list(pre.parents)[:3])
    for node in nodes:
        if not isinstance(node, Tag):
            continue
        lang = _lang_from_classes(node.get("class"))
        if lang == _DEFAULT_MARK:
            return _sniff_default_language(pre.get_text())
        if lang:
            return lang
        attr = (node.get("data-language") or node.get("data-lang") or "").strip().lower()
        if attr and attr not in _LANG_IGNORE and re.fullmatch(r"[a-z][a-z0-9+#_-]{0,19}", attr):
            return _LANG_ALIASES.get(attr, attr)
    return ""


def html_to_markdown(html: str, source_url: Optional[str] = None) -> str:
    """HTML → markdown: strip chrome, pick content root, convert with markdownify (ATX headings, fenced code, strip empty anchors). Empty string on bad input."""
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
        code_language_callback = code_language,
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
