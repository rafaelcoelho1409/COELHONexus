"""Shared pipeline panel — 4 live progress bars + current-video card.

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


def _Bar(prefix: str, title: str, hint: str, show_llm_usage: bool = False):
    """One row of the 4-phase pipeline panel — title + percentage label,
    fill bar, counter line, status text. Reused for `playwright`,
    `elasticsearch`, `qdrant`, `neo4j`. `prefix` namespaces all ids so
    JS targets `ycs-bar-{prefix}-*`.

    `show_llm_usage` (Neo4j only) adds a small button opening the
    side-view LLM-usage drawer (`_YcsLlmUsageDrawer()` below) — same
    `.fw-drawer`/`.dd-llm-rail-*` classes + JS rendering functions
    (`kpiGrid`/`modelTable`, `static/js/dd/shared/llm_totals.js`)
    DD/RR's own LLM-usage drawers already use, following the exact
    pattern RR's `_LlmUsageDrawer()` established (`features/rr/
    pipeline.py`) rather than inventing a fourth variant. Was an
    always-expanded inline section in the bar itself; moved to a
    drawer on request — the bar row stays compact, the table opens on
    demand."""
    children = [
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
            *(
                [Span(hint, id = f"ycs-bar-{prefix}-hint", cls = "ycs-bar-hint")]
                + (
                    [Button(
                        "LLM usage",
                        id = f"ycs-bar-{prefix}-llm-open",
                        cls = "ycs-bar-llm-btn",
                        type = "button",
                        title = "Open LLM usage (COELHO LLM Rotator)",
                    )] if show_llm_usage else []
                )
            ),
            cls = "ycs-bar-meta",
        ),
    ]
    return Div(
        *children,
        cls = "ycs-bar-row",
        id  = f"ycs-bar-{prefix}",
        data_phase = prefix,
    )


def _YcsLlmUsageDrawer():
    """Right-anchored slide-out for the Neo4j phase's LLM usage — same
    structure as RR's `_LlmUsageDrawer()` (`features/rr/pipeline.py`),
    own ids, single section (YCS has one call type — full-transcript
    extraction — so no per-node/chapter breakdown is needed, unlike
    DD's 3-section drawer). Hydrated by `static/js/ycs/llm_usage.js`."""
    return Div(
        Div(
            Div(
                Div("LLM usage", id = "ycs-llm-drawer-name",
                    cls = "fw-drawer-name"),
                Div("COELHO LLM Rotator usage for this run's Neo4j extraction.",
                    id = "ycs-llm-drawer-meta", cls = "fw-drawer-meta"),
                cls = "fw-drawer-title",
            ),
            Div(
                Button(
                    "✕",
                    type = "button",
                    cls  = "fw-drawer-btn",
                    id   = "ycs-llm-drawer-close-btn",
                    **{"aria-label": "Close LLM usage drawer"},
                ),
                cls = "fw-drawer-controls",
            ),
            cls = "fw-drawer-header",
        ),
        Div(
            Div(
                Div("Neo4j extraction LLM usage", cls = "dd-llm-rail-label"),
                Div(
                    Div("No LLM usage recorded yet.", cls = "dd-llm-rail-empty"),
                    id  = "ycs-llm-drawer-totals",
                    cls = "dd-llm-rail-host",
                ),
                id  = "ycs-llm-drawer-neo4j-section",
                cls = "dd-llm-rail-section",
            ),
            id = "ycs-llm-drawer-body", cls = "fw-drawer-body",
        ),
        id  = "ycs-llm-drawer",
        cls = "fw-drawer",
    )


def _VideoDrawer():
    """Right-side slide-out drawer carrying the per-video × per-store
    status table. 6 columns: Video (title + channel) · PW · ES · Qdrant ·
    Neo4j · Duration. Each store cell holds its own status pill
    (Queued / Running / Done / Failed / Skipped) derived per-phase —
    PW from the transcription fetch, ES gated on chunk-commit (so it
    trails PW per chunk, matching the Phase 2 bar), Qdrant/Neo4j from
    their streaming aggregators. Replaces the single conflated
    row-level pill that was misleading (it only updated when Neo4j
    finished, never showed ES/Qdrant progress).

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
                    Span("PW",      cls = "ycs-pipe-table-h",
                         title = "Playwright transcript fetch"),
                    Span("ES",      cls = "ycs-pipe-table-h",
                         title = "ElasticSearch chunk-commit"),
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
    """Wave-5 polish, split into 4 bars 2026-09-13 — live progress bars
    (Playwright, ElasticSearch, Qdrant, Neo4j) + a sticky "current
    video" metadata card, fed by 3 simultaneous Celery task polls
    (Playwright + ElasticSearch share one task id — `extract_videos` —
    since ES-indexing isn't a separate task; the two bars are derived
    from that one task's `phase` field, see `pipeline_panel.js`).

    Rendered at the TOP of every YCS page (via `YCSPage` chrome) so a
    long-running ingest stays visible while the user navigates between
    Source / Ingest / Ask. `pipeline_panel.js` decides whether to
    show it: URL `?extract=&qdrant=&neo4j=` wins, then
    `localStorage["ycs:pipeline:active"]` (24h TTL mirroring the
    backend Redis snapshot), otherwise hidden.

    Header carries `Stop` (live-only) + `Retry` (terminal-only) +
    `Wipe cache` (deletes data) + `Dismiss` (forgets local tracking
    only, no data touched). Dismiss exists so the user can clear a
    completed-and-irrelevant panel from view without going through
    the data-destructive Wipe path."""
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
                    "and Phase 4 Neo4j skip-on-video_id."
                ),
            ),
            Button(
                "Retry",
                type     = "button",
                id       = "ycs-pipe-rerun",
                cls      = "ycs-pipe-rerun-btn",
                disabled = True,
                title    = (
                    "Re-fire the 4-phase chain over the same video "
                    "IDs. Phase 1 skips transcripts already in ES; "
                    "Phase 4 skips video IDs already tagged in Neo4j; "
                    "Phase 3 re-embeds (Qdrant upserts are idempotent "
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
                    "Document/Video nodes + their orphaned __Entity__ "
                    "nodes — AND revoke any in-flight chain phases so "
                    "a mid-LLM Phase 4 doesn't write orphans after the "
                    "wipe. Wiped videos disappear from the Library "
                    "automatically, but this panel STAYS so you can "
                    "re-dispatch via Retry. The next Retry re-runs "
                    "the chain from scratch (no Phase 1 cache hits, "
                    "no Phase 4 skip-on-video_id). Entity nodes shared "
                    "with other videos are left intact."
                ),
            ),
            Button(
                "Dismiss",
                type  = "button",
                id    = "ycs-pipe-dismiss",
                cls   = "ycs-pipe-dismiss-btn",
                title = (
                    "Remove this pipeline panel from view. Forgets the "
                    "local tracking entry only — does NOT delete any "
                    "data from ES, Qdrant or Neo4j (use Wipe cache for "
                    "that). After dismiss, the next dispatch from "
                    "Source starts a fresh panel."
                ),
            ),
            # "Videos · N" trigger button — opens the per-video × per-
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
                    "(PW / ES / Qdrant / Neo4j independent cells)."
                ),
            ),
            cls = "ycs-pipe-panel-head",
        ),
        # Horizontal bar row — 4 stepper-style stage bars side by
        # side. Pattern: PatternFly progress stepper / MUI horizontal
        # stepper for short (3–7 step) sequential flows. The detailed
        # per-video × per-store status table moved out to a right-side
        # drawer (`_VideoDrawer()`) so the panel stays compact.
        #
        # 2026-09-13: split the old combined "Playwright & ElasticSearch"
        # bar into two — both poll the SAME `extract_videos` task id
        # (there's no separate ES task), but the task now emits a
        # distinct `es_indexing` phase after Playwright's `transcription`
        # phase, so JS can derive two independent progress bars from one
        # task's poll stream. See `pipeline_panel.js`'s `_subPhasePct`.
        Div(
            _Bar(
                "playwright",
                "Phase 1 · Playwright",
                "yt-dlp metadata + Playwright transcript scrape.",
            ),
            _Bar(
                "elasticsearch",
                "Phase 2 · ElasticSearch",
                "Bulk-index fetched transcripts (chunk-grained).",
            ),
            _Bar(
                "qdrant",
                "Phase 3 · Qdrant",
                "Hybrid dense + BM25 upsert.",
            ),
            _Bar(
                "neo4j",
                "Phase 4 · Neo4j",
                "Full-transcript LLM entity extraction.",
                show_llm_usage = True,
            ),
            cls = "ycs-pipe-bars-row",
        ),
        # Drawer is a sibling of the panel content but inside the
        # same root so the JS toggle logic finds it via the panel's
        # subtree. Hidden by default; CSS slides it in from the right
        # when `.is-open` is applied.
        _VideoDrawer(),
        # Separate drawer system (shared `.fw-drawer`/`.visible` — the
        # SAME one DD/RR's own LLM-usage drawers use, not `_VideoDrawer`'s
        # own `.is-open` convention) — see `_YcsLlmUsageDrawer`'s
        # docstring for why this follows RR's pattern specifically.
        _YcsLlmUsageDrawer(),
        id    = "ycs-pipe-panel",
        cls   = "ycs-pipe-panel",
        style = "display:none;",
    )
