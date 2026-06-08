"""Shared pipeline panel — 3 live progress bars + current-video card.

Lives in `shared/` rather than inside any one stage's body because it
needs to render at the TOP of every YCS page (Source / Ingest / Ask) so
a long-running ingest stays visible while the user navigates between
tabs. JS in `static/js/ycs/pipeline_panel.js` handles the state
machine — URL params (`?extract=&qdrant=&neo4j=`) take priority, then
localStorage (`ycs:pipeline:active`, 24h TTL matching the backend
Redis snapshot), and otherwise the panel stays hidden.

Hosting it in `shared/` instead of `ingest/body.py` is the single
source of truth — same DOM ids on every page, same JS hooks, no
divergence between stages."""
from __future__ import annotations

from fasthtml.common import Button, Div, Span


def _Bar(prefix: str, title: str, hint: str):
    """One row of the 3-phase pipeline panel — title + percentage label,
    fill bar, counter line, status text. Reused for `transcripts`,
    `qdrant`, `neo4j`. `prefix` namespaces all ids so JS targets
    `ycs-bar-{prefix}-*`."""
    return Div(
        Div(
            Span(title, cls = "ycs-bar-title"),
            Span("Queued", cls = "ycs-bar-state",
                 id = f"ycs-bar-{prefix}-state"),
            Span("0%", cls = "ycs-bar-pct",
                 id = f"ycs-bar-{prefix}-pct"),
            cls = "ycs-bar-head",
        ),
        Div(
            Div(cls = "ycs-bar-fill", id = f"ycs-bar-{prefix}-fill"),
            cls = "ycs-bar-track",
        ),
        Div(
            Span(hint, id = f"ycs-bar-{prefix}-hint", cls = "ycs-bar-hint"),
            cls = "ycs-bar-meta",
        ),
        cls = "ycs-bar-row",
        id  = f"ycs-bar-{prefix}",
        data_phase = prefix,
    )


def _VideoDrawer():
    """Right-side slide-out drawer carrying the per-video × per-store
    status table. 5 columns: Video (title + channel) · ES · Qdrant ·
    Neo4j · Duration. Each store cell holds its own status pill
    (Queued / Running / Done / Failed / Skipped) derived per-phase
    from `completed_ids` / `failed_ids` / `current_item` meta —
    replaces the single conflated row-level pill that was misleading
    (it only updated when Neo4j finished, never showed ES/Qdrant
    progress).

    Drawer DOM is rendered server-side as an empty shell; JS
    (`pipeline_panel.js::_renderVideoTable`) injects table rows once
    Phase 1's `all_items` payload arrives, then re-renders on each
    poll. Hidden by default; opened by the "Videos · N" button in the
    panel head. Click outside / Escape / close button dismisses.

    Pattern: Linear issue detail, Vercel deployment panel, GitHub
    Actions job log — non-blocking, dismissible, doesn't dim the
    page (unlike a centered modal — see Userpilot 2026 modal-UX
    survey on why centered modals are wrong for ongoing progress).
    """
    return Div(
        # Scrim — light overlay that intercepts outside-clicks. NOT
        # opaque (drawer isn't blocking work, just exposing detail).
        Div(
            id  = "ycs-pipe-drawer-scrim",
            cls = "ycs-pipe-drawer-scrim",
        ),
        # The drawer panel itself.
        Div(
            Div(
                Span("Videos in this pipeline", cls = "ycs-pipe-drawer-title"),
                Span(
                    "0", id = "ycs-pipe-drawer-count",
                    cls = "ycs-pipe-drawer-count",
                ),
                Button(
                    "✕",
                    type       = "button",
                    id         = "ycs-pipe-drawer-close",
                    cls        = "ycs-pipe-drawer-close",
                    title      = "Close",
                    aria_label = "Close drawer",
                ),
                cls = "ycs-pipe-drawer-head",
            ),
            Div(
                Div(
                    Span("Video",   cls = "ycs-pipe-table-h ycs-pipe-table-h-video"),
                    Span("ES",      cls = "ycs-pipe-table-h"),
                    Span("Qdrant",  cls = "ycs-pipe-table-h"),
                    Span("Neo4j",   cls = "ycs-pipe-table-h"),
                    Span("Time",    cls = "ycs-pipe-table-h ycs-pipe-table-h-time"),
                    cls = "ycs-pipe-table-headrow",
                ),
                Div(
                    Div(
                        "Waiting for metadata…",
                        cls = "ycs-pipe-drawer-empty",
                    ),
                    id  = "ycs-pipe-table-body",
                    cls = "ycs-pipe-table-body",
                ),
                cls = "ycs-pipe-table",
            ),
            id  = "ycs-pipe-drawer",
            cls = "ycs-pipe-drawer",
        ),
        id  = "ycs-pipe-drawer-root",
        cls = "ycs-pipe-drawer-root",
    )


def PipelinePanel():
    """Wave-5 polish — 3 live progress bars (transcripts, Qdrant, Neo4j)
    + a sticky "current video" metadata card, fed by 3 simultaneous
    Celery task polls.

    Rendered at the TOP of every YCS page (via `YCSPage` chrome) so a
    long-running ingest stays visible while the user navigates between
    Source / Ingest / Ask. `pipeline_panel.js` decides whether to
    show it: URL `?extract=&qdrant=&neo4j=` wins, then
    `localStorage["ycs:pipeline:active"]` (24h TTL mirroring the
    backend Redis snapshot), otherwise hidden.

    Header carries `Stop` (live-only) + `Retry` (terminal-only). NO
    dismiss affordance — the box is intentionally persistent until
    either the localStorage TTL expires (24h) or a new dispatch
    overwrites the entry, so users can't accidentally lose visibility
    on a long-running ingest."""
    return Div(
        Div(
            Span("Pipeline", cls = "ycs-pipe-panel-title"),
            Span("", id = "ycs-pipe-panel-elapsed",
                 cls = "ycs-pipe-panel-elapsed"),
            Button(
                "Stop",
                type     = "button",
                id       = "ycs-pipe-stop",
                cls      = "ycs-pipe-stop-btn",
                disabled = True,
                title    = (
                    "Revoke unfinished phases (SIGTERM the running "
                    "task, cancel queued ones). Completed phases keep "
                    "their writes; rerun resumes via Phase 1 ES-cache "
                    "and Phase 3 Neo4j skip-on-video_id."
                ),
            ),
            Button(
                "Retry",
                type     = "button",
                id       = "ycs-pipe-rerun",
                cls      = "ycs-pipe-rerun-btn",
                disabled = True,
                title    = (
                    "Re-fire the 3-phase chain over the same video "
                    "IDs. Phase 1 skips transcripts already in ES; "
                    "Phase 3 skips video IDs already tagged in Neo4j; "
                    "Phase 2 re-embeds (Qdrant upserts are idempotent "
                    "on md5(video_id_chunk_index)). Use after a "
                    "partial failure to fill in the gaps without "
                    "re-picking videos from Search."
                ),
            ),
            Button(
                "Wipe cache",
                type     = "button",
                id       = "ycs-pipe-wipe",
                cls      = "ycs-pipe-wipe-btn",
                disabled = True,
                title    = (
                    "Delete every cached artifact for these videos — "
                    "ES metadata + transcripts, Qdrant points, Neo4j "
                    "Document/Video nodes — AND revoke any in-flight "
                    "chain phases so a mid-LLM Phase 3 doesn't write "
                    "orphans after the wipe. The next Retry re-runs "
                    "the chain from scratch (no Phase 1 cache hits, "
                    "no Phase 3 skip-on-video_id). Use after fixing a "
                    "source-side issue (DOM selector drift, model "
                    "swap, etc.) where Retry's cache-hit behavior "
                    "would otherwise skip the videos. Entity nodes "
                    "are left intact (they may be referenced by "
                    "other videos)."
                ),
            ),
            # "Videos · N" trigger button — opens the per-video × per-
            # store status drawer. The N count is updated by JS each
            # poll. Always enabled (the drawer renders empty until
            # Phase 1's all_items lands, then refreshes per poll).
            Button(
                Span("Videos", cls = "ycs-pipe-videos-btn-label"),
                Span(
                    "0",
                    id  = "ycs-pipe-videos-btn-count",
                    cls = "ycs-pipe-videos-btn-count",
                ),
                Span("→", cls = "ycs-pipe-videos-btn-arrow"),
                type  = "button",
                id    = "ycs-pipe-videos-btn",
                cls   = "ycs-pipe-videos-btn",
                title = (
                    "Show per-video × per-store status table "
                    "(ES / Qdrant / Neo4j independent cells)."
                ),
            ),
            cls = "ycs-pipe-panel-head",
        ),
        # Horizontal bar row — 3 stepper-style stage bars side by
        # side. Pattern: PatternFly progress stepper / MUI horizontal
        # stepper for short (3–7 step) sequential flows. The detailed
        # per-video × per-store status table moved out to a right-side
        # drawer (`_VideoDrawer()`) so the panel stays compact.
        Div(
            _Bar(
                "transcripts",
                "Phase 1 · ElasticSearch",
                "yt-dlp metadata + Playwright transcript scrape.",
            ),
            _Bar(
                "qdrant",
                "Phase 2 · Qdrant",
                "Hybrid dense + BM25 upsert.",
            ),
            _Bar(
                "neo4j",
                "Phase 3 · Neo4j",
                "Full-transcript LLM entity extraction.",
            ),
            cls = "ycs-pipe-bars-row",
        ),
        # Drawer is a sibling of the panel content but inside the
        # same root so the JS toggle logic finds it via the panel's
        # subtree. Hidden by default; CSS slides it in from the right
        # when `.is-open` is applied.
        _VideoDrawer(),
        id    = "ycs-pipe-panel",
        cls   = "ycs-pipe-panel",
        style = "display:none;",
    )
