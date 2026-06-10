"""Step 2 · Ingest — pipeline status (in YCSPage chrome) + library view.

Layout (June 2026 SOTA — see Lollypop stepper guide + Linear /
YouTube Studio listing patterns):

(a) Pipeline panel — horizontal 3-bar stepper rendered in `YCSPage`
    chrome (so it persists across Source / Ingest / Ask). Drives
    Stop / Retry / Wipe / now-processing card.

(b) Library — sidebar facets (Status / Channels / Languages with
    counts) + row-card list of every ingested video + per-row trash
    + bulk-action floating bar. Replaces the legacy
    Channels + Playlists grids — those were aggregated views; the
    library is the underlying flat list of work units.

(c) Legacy single-task panel (`_JobPanel`) — kept for the bare
    `/content/channel` and `/content/playlist` redirect paths the
    Channel + Playlist Source tabs still use.

The legacy Channel + Playlist facets aren't shown on this page
anymore — they're replaced by the Library's filter sidebar — but
remain reachable via `/admin/ingested-channels` for the Ask page's
channel-scope multi-select."""
from __future__ import annotations

from fasthtml.common import Button, Div, Span

from .library import LibraryPanel


def _JobPanel():
    """Legacy single-task panel — used by the bare `/videos` extract
    path (channel + playlist forms still POST there). The Videos tab
    now POSTs to `/videos/pipeline` and uses `_PipelinePanel` below."""
    return Div(
        Div(
            Span("Idle",   id = "ycs-job-status",  cls = "ycs-job-status"),
            Span("",       id = "ycs-job-phase",   cls = "ycs-job-phase"),
            Span("",       id = "ycs-job-id",      cls = "ycs-job-phase"),
            cls = "ycs-job-head",
        ),
        Div(
            Div(cls = "ycs-job-fill", id = "ycs-job-fill"),
            cls = "ycs-job-bar",
        ),
        Div(
            Span("",  id = "ycs-job-counter"),
            Span("",  id = "ycs-job-elapsed"),
            cls = "ycs-job-meta",
        ),
        Div(
            "", id = "ycs-job-summary", cls = "ycs-job-summary",
        ),
        Div(
            Button(
                "Ingest into Qdrant",
                id   = "ycs-job-followup",
                cls  = "btn-outline",
                type = "button",
                disabled = True,
            ),
            cls = "ycs-job-actions",
        ),
        id    = "ycs-job-box",
        cls   = "ycs-job",
        style = "display:none;",
    )


def IngestBody(slug: str | None):
    # Pipeline panel lives in YCSPage chrome (rendered on every YCS
    # stage page) so it persists across Source / Ingest / Ask
    # navigation. See `shared/pipeline_panel.py`.
    #
    # Channels + Playlists aggregations were replaced 2026-06-08 by
    # the Library's filter sidebar + flat row list. The Ask page
    # still hits `/admin/ingested-channels` for its scope multi-
    # select, so the legacy endpoint isn't going away — just the
    # surface on THIS page.
    return Div(
        _JobPanel(),
        LibraryPanel(),
        cls = "ycs-ingest-body",
    )
