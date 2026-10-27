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
    _callouts_to_blockquotes(soup)
    _drop_viewcode_links(soup)
    for sel in params.CHROME_SELECTORS:
        furniture = sel in params.HEADING_FURNITURE_SELECTORS
        for el in soup.select(sel):
            # Inside a heading, a selector that is not heading furniture would take the heading's own text with it.
            if not furniture and el.find_parent(list(params.HEADING_TAGS)) is not None:
                continue
            el.decompose()


def _unwrap_heading_anchors(soup: BeautifulSoup) -> None:
    """A heading that is a link to itself (mdBook's `<h1><a class="header" href="#intro">Getting started</a></h1>`) is the plain
    heading: `# [Getting started](#intro)` is no better a title than `# Getting started`."""
    for heading in soup.find_all(list(params.HEADING_TAGS)):
        for link in heading.find_all("a", href = re.compile(r"^#")):
            link.unwrap()


def _drop_viewcode_links(soup: BeautifulSoup) -> None:
    """Sphinx's `[source]` link after every signature goes with its `<a>`, which would otherwise be left empty."""
    for span in soup.select(params.VIEWCODE_SELECTOR):
        link = span.find_parent("a")
        (link or span).decompose()


def _callouts_to_blockquotes(soup: BeautifulSoup) -> None:
    """`<aside>` is in CHROME_SELECTORS — most are sidebars and tables of contents — but Starlight (Terragrunt) writes its
    Note / Tip / Caution callouts as `<aside class="starlight-aside …">`, and they went with the rest: 3 of 3 Notes on the
    sample page, and code examples inside callouts (17 of 62 code blocks of one reference page). An aside whose class, role
    or label says callout becomes a `<blockquote>` with its title in bold; one that also says sidebar / contents stays chrome."""
    for aside in soup.find_all("aside"):
        marker = " ".join([*(aside.get("class") or []), aside.get("role") or "", aside.get("aria-label") or ""]).lower()
        if not any(m in marker for m in params.CALLOUT_ASIDE_MARKERS) or any(m in marker for m in params.NOT_CALLOUT_MARKERS):
            continue
        _as_blockquote(soup, aside)
    # …and a callout written as a `<div>`: Sphinx's `<div class="admonition note"><p class="admonition-title">Note</p>…` came out as a
    # bare "Note" line over the text (45 pages of XGBoost).
    for div in soup.find_all("div"):
        classes = div.get("class") or []
        if any(c.startswith(params.ADMONITION_CLASS_PREFIXES) for c in classes):
            _as_blockquote(soup, div)


def _as_blockquote(soup: BeautifulSoup, node: Tag) -> None:
    """A callout element as a `<blockquote>`, its icon gone and its title (`<p class="… title …">`) in bold."""
    for icon in node.find_all("svg"):
        icon.decompose()
    title = node.find(class_=re.compile(r"(^|[-_])title($|[-_])", re.I))
    if title is not None and title.find_parent(["pre", "code"]) is None:
        text = title.get_text(" ", strip = True)
        if text:
            strong = soup.new_tag("strong")
            strong.string = text
            title.clear()
            title.append(strong)
    node.name = "blockquote"
    node.attrs = {}


def _prepare_tables(soup: BeautifulSoup) -> None:
    """Make a table cell survive as one Markdown cell: a one-line `<pre>` in it (nginx's `Default: gzip off;`) is inline code,
    not a fenced block squeezed into a cell, and a `|` in its text is escaped (`gzip on | off;` was split into two cells)."""
    for cell in soup.find_all(["td", "th"]):
        for pre in cell.find_all("pre"):
            text = pre.get_text().strip()
            if text and "\n" not in text:
                code = soup.new_tag("code")
                code.string = text
                pre.replace_with(code)
        for node in list(cell.find_all(string = True)):
            if "|" in node and type(node) is NavigableString:
                node.replace_with(NavigableString(str(node).replace("|", "\\|")))


def _heading_text(root) -> str:
    """The text of the first `<h1>` under `root`, on one line ("" if there is none)."""
    h1 = root.find("h1")
    if h1 is None:
        return ""
    return re.sub(r"\s+", " ", h1.get_text(" ", strip = True).replace("​", "")).strip()


def page_name(title: str) -> str:
    """The page's own name out of a `<title>`: what is before the last separator ("Quick start | Grafana Tempo
    documentation" → "Quick start"). The title as it is when it has none, or when cutting it would leave nothing."""
    best = -1
    cut = ""
    for sep in params.TITLE_SEPARATORS:
        at = title.rfind(sep)
        if at > best:
            best, cut = at, sep
    return title[:best].strip() or title.strip() if best > 0 else title.strip()


_FENCED_RE = re.compile(r"^(`{3,}|~{3,}).*?^\1[ \t]*$", re.M | re.S)
_ATX_RE = re.compile(r"^(#{1,6})[ \t]+(.+?)[ \t]*#*[ \t]*$", re.M)


def _words(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", text.replace("’", "'").lower()).strip()


def ensure_h1(markdown: str, title: str) -> str:
    """A page that comes out with no H1 gets one: nginx.org writes a page's name as an `<h2>`, so its first heading is that
    name — promoted when the page's `<title>` contains it ("nginx: Beginner's Guide" / "## Beginner’s Guide") — and
    otherwise the `<title>`'s page name is put on top. Without one a page that is split into sections has no parent to name
    them by. A page that already has an H1 (outside code), or one with no title to go by, is left as it is."""
    name = page_name(title or "")
    if not name:
        return markdown
    prose = _FENCED_RE.sub("", markdown)
    if re.search(r"^# \S", prose, re.M):
        return markdown
    first = _ATX_RE.search(markdown)
    head = markdown[: first.start()] if first else ""
    if first and not head.strip() and _words(first.group(2)) and _words(first.group(2)) in _words(title):
        return f"# {first.group(2)}{markdown[first.end():]}"
    return f"# {name}\n\n{markdown}"


def _heading_to_page_h1(soup: BeautifulSoup, root, heading: str) -> None:
    """Put back the page's `<h1>` when stripping chrome took it: Docusaurus and Mintlify keep it in a `<header>`, which is
    in CHROME_SELECTORS, so every page of Playwright, deepagents and langgraph came out with no H1."""
    if heading and root.find("h1") is None:
        h1 = soup.new_tag("h1")
        h1.string = heading
        root.insert(0, h1)


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


def html_to_markdown(html: str, source_url: Optional[str] = None, default_code_language: str = "") -> str:
    """HTML → markdown: strip chrome, pick content root, convert with markdownify (ATX headings, fenced code, strip empty anchors). Empty string on bad input.
    `default_code_language` labels a code block whose markup names no language (go.dev's, typescriptlang.org's and SHAP's
    notebooks' `<pre>` have no class: 929 of Go's 932 fences, all 257 of TypeScript's were unlabelled) — what the catalog says
    the docs are written in; a block that looks like a shell session is not labelled."""
    if not html or not html.strip():
        return ""
    try:
        soup = BeautifulSoup(html, "lxml")
    except Exception as e:
        logger.info(f"[extract] lxml parse failed for {source_url}: {e}; "
                    f"falling back to html.parser")
        soup = BeautifulSoup(html, "html.parser")

    page_title = soup.title.get_text(" ", strip = True) if soup.title else ""
    for sel in params.PERMALINK_SELECTORS:
        for el in soup.select(sel):
            el.decompose()
    # The page's own <h1>, from inside its content element — with no such element the whole <body> is the root, and its first
    # <h1> may be the site's logo in a banner, which is not the page's heading.
    content = find_content_root(soup)
    heading = "" if content is soup or content is soup.body else _heading_text(content)
    strip_chrome(soup)
    _unwrap_heading_anchors(soup)
    _normalize_math_to_markdown(soup)
    _prepare_tables(soup)
    root = find_content_root(soup)
    _heading_to_page_h1(soup, root, heading)

    def language_of(pre: Tag) -> str:
        found = code_language(pre)
        if found or not default_code_language:
            return found
        return "" if _looks_like_shell(pre.get_text()) else default_code_language

    md = _md(
        str(root),
        heading_style = "ATX",
        code_language = "",
        code_language_callback = language_of,
        bullets = "*-+",
        strip = ["script", "style"],
    )
    return ensure_h1(_collapse_blank_lines(md).strip(), page_title)


_SHELL_START_RE = re.compile(r"^\s*(?:\$|#!|>>>|%|(?:sudo|pip3?|npm|npx|yarn|pnpm|brew|apt(?:-get)?|go (?:get|install|run|build|mod|test)|git|cd|mkdir|curl|wget|export|docker|make)\s)")


def _looks_like_shell(text: str) -> bool:
    """A code block that is a shell session or a command (`$ go run x.go`, `npm install typescript`), not source in the docs' language."""
    return bool(_SHELL_START_RE.match(text or ""))


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
