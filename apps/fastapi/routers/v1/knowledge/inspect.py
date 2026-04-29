"""
Markdown quality inspector for the KD ingestion pipeline.

Reads from `{user_id}/knowledge/{slug}/research/raw/*.md` (the canonical
prefix populated by `tasks/knowledge/ingestion.run_knowledge_ingestion`)
and serves browser-friendly fragments so we can audit the extracted
markdown without downloading the bucket. All quality issues observed
here directly inform extractor fixes (markdown_extractor.py chrome
stripping, Crawl4AI generator config, sitemap filters, etc.).

Output is HTML fragments — designed for HTMX `hx-swap` against panes in
`apps/web/templates/kd_inspect.templ`. Server-side rendering keeps the
frontend dependency-free (no marked.js / Shiki) and gives consistent
output across browsers.

Endpoints (under /api/v1/knowledge):
    GET /inspect/frameworks
        HTML fragment — list of framework rows (slug + raw md count).
    GET /inspect/frameworks/{slug}/files
        HTML fragment — list of .md files under research/raw/.
    GET /inspect/frameworks/{slug}/render?path=...
        HTML fragment — quality-stats header + rendered <article>.
    GET /inspect/frameworks/{slug}/raw?path=...
        text/markdown raw source for "view source" toggle.
"""
from __future__ import annotations

import logging
import re
from html import escape

import markdown
from fastapi import APIRouter, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, PlainTextResponse


logger = logging.getLogger(__name__)
router = APIRouter()


# Markdown rendering tuned for technical docs. `noclasses=True` inlines
# Pygments token styles so we don't need a separate CSS asset shipped to
# the browser.
_MD_EXTENSIONS = [
    "fenced_code",
    "tables",
    "codehilite",
    "toc",
    "admonition",
    "sane_lists",
]
_MD_EXT_CONFIGS = {
    "codehilite": {
        "guess_lang": False,
        "noclasses": True,
        "pygments_style": "github-dark",
        "linenums": False,
    },
}


_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9._-]*$")


def _safe_slug(slug: str) -> str:
    if not _SLUG_RE.match(slug):
        raise HTTPException(status_code = 400, detail = "invalid slug")
    return slug


def _safe_path(path: str) -> str:
    if ".." in path or path.startswith("/") or path.endswith("/"):
        raise HTTPException(status_code = 400, detail = "invalid path")
    return path


# =============================================================================
# GET /inspect/frameworks — framework rail
# =============================================================================
@router.get("/inspect/frameworks", response_class = HTMLResponse)
async def list_frameworks(
    request: Request,
    user_id: str = "default") -> HTMLResponse:
    """List ingested framework slugs under {user_id}/knowledge/."""
    storage = request.app.state.study_storage
    prefix = f"{user_id}/knowledge/"
    keys = await storage.list(prefix)

    frameworks: dict[str, dict] = {}
    for key in keys:
        rel = key[len(prefix):]
        if "/" not in rel:
            continue
        slug = rel.split("/", 1)[0]
        f = frameworks.setdefault(slug, {"slug": slug, "raw_md": 0, "bytes": 0})
        if rel.startswith(f"{slug}/research/raw/") and key.endswith(".md"):
            f["raw_md"] += 1

    rows = sorted(frameworks.values(), key = lambda f: f["slug"])
    if not rows:
        return HTMLResponse(
            '<div class="text-xs text-base-content/50 px-3 py-4">'
            'No ingested frameworks found. Run <code>POST /api/v1/knowledge/'
            'ingestion</code> to create one.'
            '</div>'
        )

    html = "".join(_render_framework_row(r, user_id) for r in rows)
    return HTMLResponse(html)


def _render_framework_row(row: dict, user_id: str) -> str:
    slug = escape(row["slug"])
    raw_md = row["raw_md"]
    return (
        f'<button type="button" '
        f'hx-get="/api/kd/inspect/frameworks/{slug}/files?user_id={escape(user_id)}" '
        f'hx-target="#kd-file-list" '
        f'hx-swap="innerHTML" '
        f'hx-on:click="document.querySelectorAll(\'#kd-framework-list button\').forEach(b=>b.classList.remove(\'bg-base-200\',\'text-primary\'));this.classList.add(\'bg-base-200\',\'text-primary\');document.getElementById(\'kd-file-pane-title\').textContent=\'{slug}\';" '
        f'class="text-left px-3 py-2 rounded-md hover:bg-base-200 flex items-center justify-between gap-2 w-full text-sm transition-colors">'
        f'<span class="truncate">{slug}</span>'
        f'<span class="text-[0.65rem] text-base-content/60 shrink-0 font-mono">{raw_md} md</span>'
        f'</button>'
    )


# =============================================================================
# GET /inspect/frameworks/{slug}/files — file rail
# =============================================================================
@router.get("/inspect/frameworks/{slug}/files", response_class = HTMLResponse)
async def list_files(
    slug: str,
    request: Request,
    user_id: str = "default") -> HTMLResponse:
    """List .md files under research/raw/ for a framework slug."""
    slug = _safe_slug(slug)
    storage = request.app.state.study_storage
    prefix = f"{user_id}/knowledge/{slug}/research/raw/"
    keys = await storage.list(prefix)

    paths = sorted(k[len(prefix):] for k in keys if k.endswith(".md"))
    if not paths:
        return HTMLResponse(
            '<div class="text-xs text-base-content/50 px-3 py-4">'
            'No <code>.md</code> files found under <code>research/raw/</code>.'
            '</div>'
        )

    html = "".join(_render_file_row(slug, p, user_id) for p in paths)
    return HTMLResponse(html)


def _render_file_row(slug: str, path: str, user_id: str) -> str:
    safe_slug = escape(slug)
    safe_path = escape(path)
    return (
        f'<button type="button" '
        f'data-file-row="{safe_path}" '
        f'hx-get="/api/kd/inspect/frameworks/{safe_slug}/render?path={safe_path}&user_id={escape(user_id)}" '
        f'hx-target="#kd-preview" '
        f'hx-swap="innerHTML" '
        f'hx-on:click="document.querySelectorAll(\'#kd-file-list button\').forEach(b=>b.classList.remove(\'bg-base-200\',\'text-primary\'));this.classList.add(\'bg-base-200\',\'text-primary\');" '
        f'class="text-left px-3 py-1.5 rounded-md hover:bg-base-200 text-xs font-mono truncate w-full transition-colors">'
        f'{safe_path}'
        f'</button>'
    )


# =============================================================================
# GET /inspect/frameworks/{slug}/raw — raw markdown source
# =============================================================================
@router.get("/inspect/frameworks/{slug}/raw", response_class = PlainTextResponse)
async def get_raw(
    slug: str,
    request: Request,
    path: str = Query(...),
    user_id: str = "default") -> PlainTextResponse:
    """Raw markdown text — for view-source / copy-paste workflows."""
    slug = _safe_slug(slug)
    path = _safe_path(path)
    storage = request.app.state.study_storage
    key = f"{user_id}/knowledge/{slug}/research/raw/{path}"
    try:
        text = await storage.read_text(key)
    except Exception as e:
        logger.warning(f"[inspect] raw read failed {key!r}: {e}")
        raise HTTPException(status_code = 404, detail = "file not found")
    return PlainTextResponse(text, media_type = "text/markdown; charset=utf-8")


# =============================================================================
# GET /inspect/frameworks/{slug}/render — rendered HTML fragment
# =============================================================================
@router.get("/inspect/frameworks/{slug}/render", response_class = HTMLResponse)
async def render_md(
    slug: str,
    request: Request,
    path: str = Query(...),
    user_id: str = "default") -> HTMLResponse:
    """
    Render an extracted .md file to an HTML fragment for HTMX swap.

    Stitches a quality-stats header on top of the rendered body so we can
    spot-check extraction issues at a glance (low word count = thin page,
    zero code blocks on a CLI-reference page = chrome problem, etc.).
    """
    slug = _safe_slug(slug)
    path = _safe_path(path)
    storage = request.app.state.study_storage
    key = f"{user_id}/knowledge/{slug}/research/raw/{path}"
    try:
        text = await storage.read_text(key)
    except Exception as e:
        logger.warning(f"[inspect] render read failed {key!r}: {e}")
        raise HTTPException(status_code = 404, detail = "file not found")

    # Cheap quality signals — exact enough for visual triage.
    word_count = len(re.findall(r"\w+", text))
    code_blocks = len(re.findall(r"^```", text, re.MULTILINE)) // 2
    headings = len(re.findall(r"^#{1,6}\s", text, re.MULTILINE))
    link_count = len(re.findall(r"\[([^\]]+)\]\(([^)]+)\)", text))

    body_html = markdown.markdown(
        text,
        extensions = _MD_EXTENSIONS,
        extension_configs = _MD_EXT_CONFIGS,
    )

    return HTMLResponse(
        _render_preview(
            slug = slug,
            path = path,
            user_id = user_id,
            body_html = body_html,
            word_count = word_count,
            code_blocks = code_blocks,
            headings = headings,
            link_count = link_count,
            chars = len(text),
        )
    )


def _render_preview(
    slug: str,
    path: str,
    user_id: str,
    body_html: str,
    word_count: int,
    code_blocks: int,
    headings: int,
    link_count: int,
    chars: int) -> str:
    safe_path = escape(path)
    safe_slug = escape(slug)
    safe_user = escape(user_id)
    return f"""
<header class="flex flex-wrap items-center gap-x-4 gap-y-1 text-xs text-base-content/70 mb-4 pb-3 border-b border-base-300 sticky top-0 bg-base-200/95 backdrop-blur z-10 -mx-8 -mt-8 px-8 pt-6 pb-3">
  <span class="font-mono text-base-content/90 truncate flex-1 min-w-0">{safe_path}</span>
  <span class="opacity-40">·</span>
  <span><strong>{word_count:,}</strong> words</span>
  <span><strong>{code_blocks}</strong> code</span>
  <span><strong>{headings}</strong> headings</span>
  <span><strong>{link_count}</strong> links</span>
  <span><strong>{chars:,}</strong> bytes</span>
  <a href="/api/kd/inspect/frameworks/{safe_slug}/raw?path={safe_path}&user_id={safe_user}"
     target="_blank" rel="noopener"
     class="link link-hover text-primary text-xs ml-2">view source</a>
</header>
<article class="prose prose-zinc dark:prose-invert prose-sm md:prose-base max-w-none prose-pre:bg-zinc-900 prose-pre:text-zinc-100 prose-headings:scroll-mt-20 prose-a:text-primary">
{body_html}
</article>
"""
