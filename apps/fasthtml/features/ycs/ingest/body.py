"""Step 2 · Ingest — task polling + library view.

Two server-rendered regions, populated by `ingest.js`:

(a) Active job panel — shown when the URL has `?task=<id>` (set by
    a redirect from the Source step's dispatch endpoints). JS polls
    `/api/v1/ycs/admin/task/{id}` and renders status / progress /
    final result. After a SUCCESS, it offers an "Ingest into Qdrant"
    follow-on action (queues `/api/v1/ycs/agents/ingest/qdrant`).

(b) Library view — two grids fed by ES aggregations
    (`/api/v1/ycs/admin/ingested-channels` + `/ingested-playlists`).
    Cards show channel/playlist name + video count and let the user
    queue Qdrant or Neo4j ingestion for a subset.

Fresh code — deprecated had no FastHTML; the deprecated `tasks` router
returned task IDs but had no surfaced UI."""
from __future__ import annotations

from fasthtml.common import Button, Div, H2, Option, P, Select, Span


def _JobPanel():
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


def _IngestPipelinePanel():
    """Trigger Qdrant / Neo4j ingestion explicitly. Useful for the user
    who already extracted videos via the Source step and wants to
    re-run the embedding stage (e.g. after changing chunk parameters)."""
    return Div(
        H2("Pipeline", style = "margin: 0 0 8px 0; font-weight: 500;"),
        P(
            "Re-run individual stages over the videos already in "
            "Elasticsearch. Each click dispatches a Celery task.",
            cls = "ycs-intro",
        ),
        Div(
            Div(
                Button(
                    "Ingest all into Qdrant",
                    id   = "ycs-pipe-qdrant",
                    cls  = "btn-outline",
                    type = "button",
                ),
                Button(
                    "Extract graph (Neo4j)",
                    id   = "ycs-pipe-neo4j",
                    cls  = "btn-outline",
                    type = "button",
                ),
                Span("", id = "ycs-pipe-status", cls = "ycs-search-status"),
                cls = "ycs-form-actions",
            ),
            cls = "ycs-pipe-actions",
        ),
    )


def _LibrarySection(title: str, container_id: str, empty: str):
    return Div(
        H2(title, style = "margin: 18px 0 8px 0; font-weight: 500;"),
        Div(
            Div(empty, cls = "ycs-empty-card"),
            id    = container_id,
            cls   = "ycs-lib-grid",
        ),
    )


def _ChannelPicker():
    """Lets the user kick off Qdrant ingestion for a specific channel
    rather than the whole index. Populated from
    `/api/v1/ycs/admin/ingested-channels` on first load."""
    return Div(
        Select(
            Option("— All channels —", value = ""),
            id   = "ycs-channel-filter",
            cls  = "ycs-filter-select",
        ),
        cls = "ycs-filter-field",
    )


def IngestBody(slug: str | None):
    return Div(
        H2("Ingest", style = "margin: 0 0 8px 0; font-weight: 500;"),
        P(
            "Watch in-flight ingestion jobs, browse what's already in "
            "your library, and re-run individual pipeline stages.",
            cls = "ycs-intro",
        ),
        _JobPanel(),
        _IngestPipelinePanel(),
        _LibrarySection(
            "Channels",
            "ycs-channels-grid",
            "No channels ingested yet. Start from the Source step.",
        ),
        _LibrarySection(
            "Playlists",
            "ycs-playlists-grid",
            "No playlists ingested yet.",
        ),
    )
