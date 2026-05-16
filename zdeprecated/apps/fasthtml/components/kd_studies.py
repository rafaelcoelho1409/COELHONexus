"""
Knowledge Distiller — Studies viewer (2026-05-12 night).

Two pages:
  - KDStudiesListPage: table of all studies from Redis registry, newest first.
    Auto-refreshes every 10s via HTMX. Click a row → detail page.
  - KDStudyDetailPage(study_id): study metadata + chapter list with status.
    Each chapter is collapsible — click to lazy-load its README + challenges
    + flashcards from FastAPI's /studies/{id}/chapters/{n} endpoint. Markdown
    rendered client-side via marked.js + highlight.js (loaded from CDN) so
    we don't need a Python markdown lib in pyproject.

Talks to upstream FastAPI via the reverse-proxy pattern already used in
kd.py's /api/kd/inspect/* routes.
"""
from fasthtml.common import (
    A, Article, Aside, Button, Code, Div, Form, H1, H2, H3, H4, I, Input,
    Label, Li, Nav, Option, P, Pre, Script, Section, Select, Span, Style,
    Table, Tbody, Td, Th, Thead, Title, Tr, Ul, NotStr,
)

from components.base import Page


# =============================================================================
# Client-side markdown renderer — marked.js + highlight.js via CDN
# =============================================================================
# Loaded once per page; the renderChapter() function is called by HTMX after
# the chapter content swap. Keeps the Python side dependency-free.
_MARKDOWN_RENDERER_JS = """
function _renderMarkdownIn(rootEl) {
  if (!rootEl || !window.marked) return;
  rootEl.querySelectorAll('.kd-md-raw').forEach(function(node) {
    if (node.dataset.rendered === '1') return;
    const raw = node.textContent || '';
    try {
      node.innerHTML = window.marked.parse(raw, { gfm: true, breaks: false });
      node.dataset.rendered = '1';
      // Re-run highlight.js on any new <pre><code> blocks
      if (window.hljs) {
        node.querySelectorAll('pre code').forEach(function(blk) {
          try { window.hljs.highlightElement(blk); } catch (e) {}
        });
      }
    } catch (e) {
      console.warn('markdown render failed', e);
    }
  });
}

// Run on initial page load
document.addEventListener('DOMContentLoaded', function () {
  _renderMarkdownIn(document.body);
});

// Re-run after every HTMX swap (chapter lazy-load fires this).
// <details> open-state preservation across the 15s chapter-list poll is
// now handled by idiomorph (hx_ext="morph" on #kd-study-chapters in
// kd_studies.py). Idiomorph morphs the DOM in-place instead of replacing
// innerHTML, so native element state survives polled re-renders — no
// localStorage shim needed.
document.body.addEventListener('htmx:afterSwap', function (evt) {
  _renderMarkdownIn(evt.detail.target || document.body);
  if (window.lucide) window.lucide.createIcons();
});
"""

_MARKDOWN_HEAD = (
    # marked.js — lightweight markdown → HTML
    Script(src="https://cdn.jsdelivr.net/npm/marked@13.0.3/marked.min.js"),
    # highlight.js — syntax highlight code blocks
    Script(src="https://cdnjs.cloudflare.com/ajax/libs/highlight.js/11.10.0/highlight.min.js"),
    NotStr(
        '<link rel="stylesheet" '
        'href="https://cdnjs.cloudflare.com/ajax/libs/highlight.js/11.10.0/styles/github.min.css" '
        'media="(prefers-color-scheme: light)">'
    ),
    NotStr(
        '<link rel="stylesheet" '
        'href="https://cdnjs.cloudflare.com/ajax/libs/highlight.js/11.10.0/styles/github-dark.min.css" '
        'media="(prefers-color-scheme: dark)">'
    ),
    Script(_MARKDOWN_RENDERER_JS),
    # Markdown-rendered content gets typography styles
    Style("""
    .kd-md-raw { white-space: normal; }
    .kd-md-raw[data-rendered='1'] h1 { font-size: 1.6rem; font-weight: 700; margin: 1rem 0 0.6rem; }
    .kd-md-raw[data-rendered='1'] h2 { font-size: 1.3rem; font-weight: 600; margin: 1rem 0 0.5rem; border-bottom: 1px solid var(--fallback-bc,#e5e7eb); padding-bottom: 0.25rem; }
    .kd-md-raw[data-rendered='1'] h3 { font-size: 1.1rem; font-weight: 600; margin: 0.8rem 0 0.4rem; }
    .kd-md-raw[data-rendered='1'] h4 { font-size: 1rem; font-weight: 600; margin: 0.6rem 0 0.3rem; }
    .kd-md-raw[data-rendered='1'] p { margin: 0.5rem 0; line-height: 1.6; }
    .kd-md-raw[data-rendered='1'] ul, .kd-md-raw[data-rendered='1'] ol { margin: 0.5rem 0; padding-left: 1.5rem; }
    .kd-md-raw[data-rendered='1'] code { background: rgba(127,127,127,0.1); padding: 0.1em 0.3em; border-radius: 3px; font-size: 0.9em; }
    .kd-md-raw[data-rendered='1'] pre { background: #0d1117; color: #e6edf3; padding: 0.75rem 1rem; border-radius: 6px; overflow-x: auto; margin: 0.6rem 0; font-size: 0.85rem; }
    .kd-md-raw[data-rendered='1'] pre code { background: transparent; padding: 0; font-size: 0.85rem; }
    .kd-md-raw[data-rendered='1'] blockquote { border-left: 3px solid var(--fallback-bc,#e5e7eb); padding-left: 1rem; margin: 0.6rem 0; opacity: 0.85; }
    .kd-md-raw[data-rendered='1'] a { color: var(--fallback-p,#2563eb); text-decoration: underline; }
    """),
)


# =============================================================================
# Status badge helpers
# =============================================================================
def _status_badge(task_state: str | None, phase: str | None) -> Span:
    """Render a colored badge for a study's current state."""
    if task_state == "SUCCESS":
        return Span("complete", cls="badge badge-success badge-sm")
    if task_state == "FAILURE":
        return Span("failed", cls="badge badge-error badge-sm")
    if task_state in ("PENDING", "STARTED", "RETRY"):
        label = f"{phase or task_state.lower()}"
        return Span(label, cls="badge badge-info badge-sm")
    return Span(task_state or "?", cls="badge badge-ghost badge-sm")


# =============================================================================
# List page — table of all studies
# =============================================================================
def KDStudiesListPage():
    """Auto-refreshing table of all studies from Redis registry."""
    return Page(
        "Studies · KD",
        Section(
            Div(
                Div(
                    H1("Studies", cls="text-2xl font-bold"),
                    P(
                        "Live registry from Redis. Refreshes every 10 seconds.",
                        cls="text-sm text-base-content/60",
                    ),
                    cls="mb-4",
                ),
                Div(
                    # The table itself lives in #kd-studies-table; HTMX polls
                    # the FastAPI list endpoint (proxied via /api/kd/studies)
                    # every 10s and swaps the body.
                    Div(
                        Div(
                            I(data_lucide="loader",
                              cls="w-5 h-5 animate-spin opacity-60"),
                            Span("Loading studies...", cls="text-sm opacity-60"),
                            cls="flex items-center gap-2 p-6",
                        ),
                        id="kd-studies-table",
                        hx_get="/api/kd/studies/list_fragment",
                        hx_trigger="load, every 10s",
                        hx_swap="innerHTML",
                    ),
                    cls="bg-base-100 border border-base-300 rounded-lg overflow-hidden",
                ),
                cls="max-w-6xl mx-auto px-6 py-8",
            ),
            cls="min-h-screen",
        ),
        active_nav="kd-studies",
    )


def KDStudiesTableFragment(studies: list[dict], total: int):
    """The table body — refreshed by HTMX every 10s."""
    if not studies:
        return Div(
            Div(
                I(data_lucide="inbox", cls="w-12 h-12 opacity-30 mx-auto mb-2"),
                P("No studies in registry.", cls="text-sm opacity-60"),
                P("Studies appear here once you POST /api/v1/knowledge/studies.",
                  cls="text-xs opacity-50 mt-1"),
                cls="text-center py-12",
            ),
        )
    rows = []
    for s in studies:
        sid = s.get("study_id") or ""
        rows.append(Tr(
            Td(
                A(
                    sid[:8] + "…",
                    href=f"/kd/studies/{sid}",
                    cls="link link-primary font-mono text-xs",
                ),
            ),
            Td(s.get("framework") or "—", cls="text-sm font-medium"),
            Td(s.get("level") or "—", cls="text-xs opacity-70"),
            Td(s.get("user_id") or "—", cls="text-xs opacity-70"),
            Td(_status_badge(s.get("task_state"), s.get("current_phase"))),
            Td(
                (s.get("created_at") or "")[:19].replace("T", " "),
                cls="text-xs opacity-60 font-mono",
            ),
            cls="hover:bg-base-200",
        ))
    return Div(
        Table(
            Thead(
                Tr(
                    Th("Study ID", cls="text-xs font-semibold"),
                    Th("Framework", cls="text-xs font-semibold"),
                    Th("Level", cls="text-xs font-semibold"),
                    Th("User", cls="text-xs font-semibold"),
                    Th("Status", cls="text-xs font-semibold"),
                    Th("Created", cls="text-xs font-semibold"),
                ),
            ),
            Tbody(*rows),
            cls="table table-sm",
        ),
        Div(
            f"{len(studies)} of {total} shown",
            cls="text-xs opacity-50 px-4 py-2 border-t border-base-300",
        ),
    )


# =============================================================================
# Detail page — one study, with chapter list
# =============================================================================
def KDStudyDetailPage(study_id: str):
    """Detail view for one study. Chapter cards lazy-load via HTMX."""
    return Page(
        f"{study_id[:8]} · Study · KD",
        *_MARKDOWN_HEAD,
        Section(
            Div(
                # Breadcrumb back to list
                Div(
                    A(
                        I(data_lucide="arrow-left", cls="w-4 h-4"),
                        Span("All studies"),
                        href="/kd/studies",
                        cls="link link-hover text-xs flex items-center gap-1 mb-2",
                    ),
                ),
                # Study header — refreshed every 10s
                Div(
                    Div(
                        I(data_lucide="loader",
                          cls="w-5 h-5 animate-spin opacity-60"),
                        Span("Loading...", cls="text-sm opacity-60"),
                        cls="flex items-center gap-2 p-6",
                    ),
                    id="kd-study-header",
                    hx_get=f"/api/kd/studies/{study_id}/header",
                    hx_trigger="load, every 10s",
                    hx_swap="innerHTML",
                ),
                # Chapter list — initial load only; chapters lazy-load contents
                # when clicked. Uses idiomorph (morph swap) so <details>
                # open state is preserved across the 15s poll; previously
                # innerHTML-swapped which destroyed every <details> on each
                # tick. See base.py for the extension script.
                Div(
                    Div(
                        I(data_lucide="loader",
                          cls="w-5 h-5 animate-spin opacity-60"),
                        cls="p-6",
                    ),
                    id="kd-study-chapters",
                    hx_get=f"/api/kd/studies/{study_id}/chapters_list",
                    hx_trigger="load, every 15s",
                    hx_ext="morph",
                    hx_swap="morph",
                    cls="mt-6",
                ),
                cls="max-w-5xl mx-auto px-6 py-6",
            ),
            cls="min-h-screen",
        ),
        active_nav="kd-studies",
    )


def StudyHeaderFragment(study_data: dict):
    """Top card with study metadata. Refreshed by HTMX every 10s."""
    study = study_data.get("study") or {}
    snapshot = {k: v for k, v in study_data.items() if k != "study"}
    framework = study.get("framework", "?")
    profile = study.get("user_profile") or {}
    target_markets = profile.get("target_markets") or []
    mastered = profile.get("mastered_technologies") or []
    study_id = study.get("study_id", "")
    return Div(
        Div(
            Div(
                H1(framework, cls="text-2xl font-bold"),
                Div(
                    Span(f"v{study.get('version', 'latest')}",
                         cls="badge badge-outline badge-sm"),
                    Span(profile.get("level") or "—",
                         cls="badge badge-outline badge-sm"),
                    _status_badge(
                        snapshot.get("task_state"),
                        (snapshot.get("progress") or {}).get("phase"),
                    ),
                    cls="flex gap-2 mt-1 items-center flex-wrap",
                ),
                cls="flex-1",
            ),
            Div(
                Div("Study ID", cls="text-xs opacity-50"),
                Code(study_id[:16] + "…" if study_id else "—",
                     cls="text-xs font-mono"),
                cls="text-right",
            ),
            cls="flex items-start justify-between gap-4 mb-3",
        ),
        # Observability pages — one per KD node. Stages 1 (Ingestion) + 2
        # (Planner) live now; later stages (Synth / Curator / Critic /
        # Assembler / Bandit) land here as they ship. See
        # docs/KD-PIPELINE-SUBSTEP-MAP-2026-05-15.md.
        Div(
            Span("Observability:",
                 cls="text-xs opacity-50 mr-1"),
            A(
                I(data_lucide="activity", cls="w-3 h-3"),
                Span("Ingestion"),
                href=f"/kd/studies/{study_id}/observability/ingestion",
                cls="btn btn-xs btn-outline gap-1",
            ) if study_id else "",
            A(
                I(data_lucide="network", cls="w-3 h-3"),
                Span("Planner"),
                href=f"/kd/studies/{study_id}/observability/planner",
                cls="btn btn-xs btn-outline gap-1",
            ) if study_id else "",
            cls="flex items-center gap-2 mb-3 flex-wrap",
        ),
        Div(
            Div(
                Div("User", cls="text-xs opacity-50"),
                Div(study.get("user_id") or "—", cls="text-sm"),
            ),
            Div(
                Div("Target markets", cls="text-xs opacity-50"),
                Div(", ".join(target_markets) if target_markets else "—",
                    cls="text-sm"),
            ),
            Div(
                Div("Mastered", cls="text-xs opacity-50"),
                Div(", ".join(mastered[:4]) if mastered else "—",
                    cls="text-sm"),
            ),
            Div(
                Div("Created", cls="text-xs opacity-50"),
                Div((study.get("created_at") or "")[:19].replace("T", " "),
                    cls="text-sm font-mono"),
            ),
            cls="grid grid-cols-2 md:grid-cols-4 gap-4 text-sm",
        ),
        cls="bg-base-100 border border-base-300 rounded-lg p-5",
    )


def ChaptersListFragment(study_id: str, study_data: dict, tree: dict):
    """List of chapter cards. Each is collapsible + HTMX-loads its README on open."""
    # Derive expected chapter count from the plan if available; otherwise scan
    # the tree for chapterNN/ prefixes.
    plan = (
        study_data.get("study", {}).get("plan")
        or study_data.get("plan")
        or {}
    )
    plan_chapters = plan.get("chapters") if isinstance(plan, dict) else None

    # Tree gives MinIO keys — find chapter directories with README present.
    # FastAPI's /studies/{id}/tree returns keys under "objects" (per
    # routers/v1/knowledge/distiller.py:get_study_tree). The "keys"/"files"
    # fallbacks are kept for backward-compat with any older response shapes.
    keys = tree.get("objects") or tree.get("keys") or tree.get("files") or []
    chapters_with_readme = set()
    chapter_titles_from_tree: dict[int, str] = {}
    for k in keys:
        # Match chapter01/README.md, chapter02/README.md, etc.
        import re as _re
        m = _re.search(r"/chapter(\d+)/README\.md$", str(k))
        if m:
            chapters_with_readme.add(int(m.group(1)))

    # Build the ordered chapter list. Prefer plan order if we have it.
    if plan_chapters:
        chapter_entries = [
            (c.get("number"), c.get("title"), c.get("goal"))
            for c in plan_chapters
            if c.get("number") is not None
        ]
    else:
        chapter_entries = [
            (n, f"Chapter {n}", None) for n in sorted(chapters_with_readme)
        ]

    if not chapter_entries:
        return Div(
            Div(
                I(data_lucide="hourglass", cls="w-8 h-8 opacity-30 mx-auto mb-2"),
                P("No plan yet — ingestion or planning still running.",
                  cls="text-sm opacity-60 text-center"),
                cls="py-10",
            ),
            cls="bg-base-100 border border-base-300 rounded-lg",
        )

    cards = []
    for num, title, goal in chapter_entries:
        is_ready = num in chapters_with_readme
        cards.append(_ChapterCard(study_id, num, title, goal, is_ready))
    return Div(
        H2(f"Chapters ({len(chapter_entries)})",
           cls="text-lg font-semibold mb-3"),
        Div(*cards, cls="space-y-3"),
    )


def _ChapterCard(study_id: str, n: int, title: str | None, goal: str | None,
                 is_ready: bool):
    """One collapsible chapter card. README lazy-loads on first open."""
    title_str = title or f"Chapter {n}"
    status_chip = (
        Span("ready", cls="badge badge-success badge-sm")
        if is_ready
        else Span("synthing...", cls="badge badge-warning badge-sm")
    )
    # Use <details> for native collapsible behavior; HTMX loads content
    # on the first `toggle` event when ready.
    return Article(
        NotStr(f'''
<details class="bg-base-100 border border-base-300 rounded-lg group" data-chapter="{n}">
  <summary class="cursor-pointer p-4 flex items-center justify-between hover:bg-base-200 transition-colors">
    <div class="flex items-center gap-3 flex-1 min-w-0">
      <div class="text-xs font-mono opacity-60 w-8">{n:02d}</div>
      <div class="flex-1 min-w-0">
        <div class="font-medium truncate">{title_str}</div>
        {'<div class="text-xs opacity-60 truncate mt-0.5">' + (goal or "") + "</div>" if goal else ""}
      </div>
    </div>
    <div class="flex items-center gap-2 ml-4">
      {_status_badge_html(is_ready)}
      <i data-lucide="chevron-down" class="w-4 h-4 opacity-60 group-open:rotate-180 transition-transform"></i>
    </div>
  </summary>
  <div class="border-t border-base-300 px-5 py-4">
    <div hx-get="/api/kd/studies/{study_id}/chapters/{n}/render"
         hx-trigger="toggle from:closest details once"
         hx-swap="innerHTML"
         class="kd-chapter-body">
      <div class="text-sm opacity-50 py-4 text-center">
        <i data-lucide="loader" class="w-4 h-4 inline animate-spin"></i>
        <span class="ml-2">Loading…</span>
      </div>
    </div>
  </div>
</details>
'''),
    )


def _status_badge_html(is_ready: bool) -> str:
    if is_ready:
        return '<span class="badge badge-success badge-sm">ready</span>'
    return '<span class="badge badge-warning badge-sm">pending</span>'


# =============================================================================
# Chapter content fragment — rendered by HTMX after the user expands a chapter
# =============================================================================
def ChapterContentFragment(chapter_data: dict):
    """README + challenges + flashcards rendered from FastAPI's chapter endpoint."""
    content = chapter_data.get("content") or ""
    challenges = chapter_data.get("challenges") or ""
    flashcards = chapter_data.get("flashcards") or []

    chapter_md = Div(
        # Raw markdown stored in textContent — marked.js renders it client-side
        # after htmx:afterSwap fires
        Div(content, cls="kd-md-raw"),
        cls="prose max-w-none",
    )

    challenges_md = (
        Div(
            H3("Challenges", cls="text-base font-semibold mt-6 mb-2"),
            Div(challenges, cls="kd-md-raw text-sm"),
        )
        if challenges else ""
    )

    flashcards_html = ""
    if flashcards:
        items = []
        for i, fc in enumerate(flashcards, 1):
            front = fc.get("front", "")
            back = fc.get("back", "")
            items.append(
                Div(
                    Div(
                        Span(f"Q{i}", cls="badge badge-outline badge-xs mr-2"),
                        Span(front, cls="font-medium text-sm"),
                        cls="mb-1",
                    ),
                    Div(back, cls="text-sm opacity-80 pl-9"),
                    cls="py-2 border-b border-base-300 last:border-0",
                ),
            )
        flashcards_html = Div(
            H3(f"Flashcards ({len(flashcards)})",
               cls="text-base font-semibold mt-6 mb-2"),
            Div(*items, cls="bg-base-200 rounded-lg p-3"),
        )

    return Div(chapter_md, challenges_md, flashcards_html)


def ChapterErrorFragment(error: str):
    """Shown when the chapter isn't ready or fetch failed."""
    return Div(
        I(data_lucide="alert-circle",
          cls="w-5 h-5 opacity-50 inline mr-2"),
        Span(error, cls="text-sm opacity-70"),
        cls="text-center py-6",
    )
