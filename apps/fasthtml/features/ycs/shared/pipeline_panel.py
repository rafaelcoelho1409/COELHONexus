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


def _CurrentVideoCard():
    """Slim metadata card surfaced live by `pipeline_panel.js` — title +
    channel + duration + views + likes + upload date. Mirrors the
    Search-page result row minus the thumbnail (per the user spec).
    Hidden while the pipeline is queued; revealed on first progress
    event carrying `current_item`."""
    return Div(
        Div(
            Span("Now processing", cls = "ycs-vid-card-label"),
            Span("", id = "ycs-vid-card-phase",
                 cls = "ycs-vid-card-phase"),
            cls = "ycs-vid-card-head",
        ),
        Div(
            Span("", id = "ycs-vid-card-title",
                 cls = "ycs-vid-card-title"),
            Div(
                Span("", id = "ycs-vid-card-channel",
                     cls = "ycs-vid-card-channel"),
                Span("·", cls = "ycs-vid-card-sep"),
                Span("", id = "ycs-vid-card-views",
                     cls = "ycs-vid-card-meta"),
                Span("·", cls = "ycs-vid-card-sep"),
                Span("", id = "ycs-vid-card-duration",
                     cls = "ycs-vid-card-meta"),
                Span("·", cls = "ycs-vid-card-sep"),
                Span("", id = "ycs-vid-card-likes",
                     cls = "ycs-vid-card-meta"),
                Span("·", cls = "ycs-vid-card-sep"),
                Span("", id = "ycs-vid-card-date",
                     cls = "ycs-vid-card-meta"),
                cls = "ycs-vid-card-row",
            ),
            cls = "ycs-vid-card-body",
        ),
        id    = "ycs-vid-card",
        cls   = "ycs-vid-card",
        style = "display:none;",
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
            cls = "ycs-pipe-panel-head",
        ),
        _Bar(
            "transcripts",
            "Phase 1 · Transcripts (Playwright)",
            "Pulling captions via DOM scrape.",
        ),
        _Bar(
            "qdrant",
            "Phase 2 · Qdrant hybrid index",
            "Chunk → NIM dense + BM25 sparse → upsert.",
        ),
        _Bar(
            "neo4j",
            "Phase 3 · Neo4j entity graph",
            "Full-transcript LLM extraction.",
        ),
        _CurrentVideoCard(),
        id    = "ycs-pipe-panel",
        cls   = "ycs-pipe-panel",
        style = "display:none;",
    )
