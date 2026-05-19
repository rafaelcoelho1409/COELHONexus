"""YouTube Content Search — single-page Q&A over indexed YT transcripts.

Backend contract:
  POST /api/v1/youtube/runs  body={video_url, question}
  → {indexed, answer, citations, model, latency_s}

UX choices vs. docs_distiller (kept intentionally smaller for v0):
  - No multi-step wizard; one form + one answer block.
  - No SSE / live progress; the POST is ~6s, so a single sky-blue
    status banner ("Indexing transcript + answering…") fills the wait.
  - No library sidebar yet — each Ask is fully idempotent on the
    backend (index_video upserts by deterministic md5-UUID), so re-asking
    the same URL is fast and safe.

Client-side dynamic logic lives in static/js/youtube_content_search.js.
"""
from fasthtml.common import (
    Button, Div, Form, Input, Option, P, Script, Select, Span, Textarea,
)

from shell import _Shell


def _Step(n: int, label: str, active: bool = False):
    """One numbered step in the YCS wizard bar. Mirrors docs_distiller's
    `_Step` 1:1 — same DOM, same modifier classes, prefix-swapped CSS.
    JS toggles `.active` / `.completed` as the user moves through the
    Source → Ingest → Ask flow."""
    cls = "ycs-step active" if active else "ycs-step"
    return Div(
        Span(str(n), cls="ycs-step-circle"),
        Span(label, cls="ycs-step-label"),
        cls=cls,
        id=f"ycs-step-{n}",
        data_step=str(n),
    )


def _Steps():
    return Div(
        Div(
            _Step(1, "Source", active=True),
            Span(cls="ycs-step-connector"),
            _Step(2, "Ingest"),
            Span(cls="ycs-step-connector"),
            _Step(3, "Ask"),
            cls="ycs-stepper",
        ),
        cls="ycs-stepper-row",
    )


def _FilterField(label: str, ctrl):
    """Vertical label-over-control pair used inside the filter grid."""
    return Div(
        Span(label, cls="ycs-filter-label"),
        ctrl,
        cls="ycs-filter-field",
    )


def _SearchPanel():
    """Step-1 Source · Search mode.

    Mirrors the legacy POST /search filter set (zdeprecated/.../routers/
    v1/youtube/content.py). Always-visible top row carries query +
    max_results + Search button; the rest collapses into "Advanced
    filters" so the panel stays scannable.
    """
    duration_select = Select(
        Option("Any", value=""),
        Option("Under 4 minutes", value="Under 4 minutes"),
        Option("4 – 20 minutes", value="4 - 20 minutes"),
        Option("Over 20 minutes", value="Over 20 minutes"),
        id="ycs-filter-duration", cls="ycs-filter-select",
    )
    live_select = Select(
        Option("Any", value=""),
        Option("Not live (VOD)", value="not_live"),
        Option("Live now", value="is_live"),
        Option("Upcoming", value="is_upcoming"),
        Option("Was live", value="was_live"),
        Option("Post live", value="post_live"),
        id="ycs-filter-live-status", cls="ycs-filter-select",
    )
    avail_select = Select(
        Option("Any", value=""),
        Option("Public", value="public"),
        Option("Unlisted", value="unlisted"),
        Option("Members only", value="subscriber_only"),
        Option("Premium only", value="premium_only"),
        id="ycs-filter-availability", cls="ycs-filter-select",
    )
    return Div(
        Div(
            Input(
                type="search", id="ycs-search-query",
                placeholder="Search YouTube videos…",
                autocomplete="off", cls="ycs-search-input",
            ),
            Input(
                type="number", id="ycs-search-max",
                value="10", min="1", max="100",
                title="Max results (1–100)",
                cls="ycs-search-max",
            ),
            Button("Search", id="ycs-search-submit",
                   type="button", cls="btn-primary"),
            cls="ycs-search-row",
        ),
        Div(
            Button(
                Span("Advanced filters", cls="ycs-filters-toggle-label"),
                Span("▾", cls="ycs-filters-toggle-chevron"),
                id="ycs-filters-toggle", type="button",
                cls="ycs-filters-toggle",
            ),
            Div(
                _FilterField(
                    "Sort",
                    Div(
                        Input(type="checkbox", id="ycs-filter-sort-date"),
                        Span("By upload date", cls="ycs-filter-check-label"),
                        cls="ycs-filter-check-wrap",
                    ),
                ),
                _FilterField("Duration preset", duration_select),
                _FilterField(
                    "Duration min (s)",
                    Input(type="number", id="ycs-filter-duration-min",
                          min="0", cls="ycs-filter-input"),
                ),
                _FilterField(
                    "Duration max (s)",
                    Input(type="number", id="ycs-filter-duration-max",
                          min="0", cls="ycs-filter-input"),
                ),
                _FilterField(
                    "Date after",
                    Input(type="text", id="ycs-filter-date-after",
                          placeholder="YYYYMMDD or today-2weeks",
                          cls="ycs-filter-input"),
                ),
                _FilterField(
                    "Date before",
                    Input(type="text", id="ycs-filter-date-before",
                          placeholder="YYYYMMDD or today",
                          cls="ycs-filter-input"),
                ),
                _FilterField(
                    "Min views",
                    Input(type="number", id="ycs-filter-min-views",
                          min="0", cls="ycs-filter-input"),
                ),
                _FilterField(
                    "Max views",
                    Input(type="number", id="ycs-filter-max-views",
                          min="0", cls="ycs-filter-input"),
                ),
                _FilterField(
                    "Min likes",
                    Input(type="number", id="ycs-filter-min-likes",
                          min="0", cls="ycs-filter-input"),
                ),
                _FilterField("Live status", live_select),
                _FilterField("Availability", avail_select),
                _FilterField(
                    "Age limit",
                    Input(type="number", id="ycs-filter-age-limit",
                          min="0", placeholder="years",
                          cls="ycs-filter-input"),
                ),
                _FilterField(
                    "Title contains",
                    Input(type="text", id="ycs-filter-title",
                          placeholder="e.g. tutorial or ^=How to",
                          cls="ycs-filter-input"),
                ),
                _FilterField(
                    "Description contains",
                    Input(type="text", id="ycs-filter-description",
                          placeholder="e.g. langchain",
                          cls="ycs-filter-input"),
                ),
                _FilterField(
                    "Channel name",
                    Input(type="text", id="ycs-filter-channel",
                          placeholder="e.g. *=astley",
                          cls="ycs-filter-input"),
                ),
                id="ycs-filters-body", cls="ycs-filters-body",
                style="display:none;",
            ),
            cls="ycs-filters",
        ),
        id="ycs-search-panel",
        cls="ycs-search-panel",
    )


def _VideosTabBody():
    return Div(
        P(
            "Paste video IDs or URLs (one per line). Bare 11-char IDs "
            "(e.g. dQw4w9WgXcQ) work too.",
            cls="ycs-tab-hint",
        ),
        Textarea(
            "",
            id="ycs-videos-input",
            placeholder=(
                "https://www.youtube.com/watch?v=dQw4w9WgXcQ\n"
                "https://youtu.be/UF8uR6Z6KLc\n"
                "dQw4w9WgXcQ"
            ),
            rows="5",
            cls="ycs-input ycs-textarea",
        ),
        Div(
            Button(
                "Find videos", id="ycs-videos-submit",
                type="button", cls="btn-primary",
            ),
            cls="ycs-actions",
        ),
        cls="ycs-direct-form",
    )


def _PlaylistTabBody():
    return Div(
        P(
            "Paste a YouTube playlist URL or a bare playlist ID "
            "(e.g. PLrAXtmRdnEQy6...).",
            cls="ycs-tab-hint",
        ),
        Div(
            Input(
                type="text", id="ycs-playlist-input",
                placeholder=(
                    "https://www.youtube.com/playlist?list=PL... or PL..."
                ),
                autocomplete="off", cls="ycs-search-input",
            ),
            Input(
                type="number", id="ycs-playlist-max",
                value="0", min="0", max="500",
                title="Max videos (0 = all, up to 500)",
                cls="ycs-search-max",
            ),
            Button(
                "Find videos", id="ycs-playlist-submit",
                type="button", cls="btn-primary",
            ),
            cls="ycs-search-row",
        ),
        cls="ycs-direct-form",
    )


def _ChannelTabBody():
    return Div(
        P(
            "Paste a channel URL, @handle, or UCxxx ID. Lands on the "
            "channel's /videos tab (skips Shorts and Live by default).",
            cls="ycs-tab-hint",
        ),
        Div(
            Input(
                type="text", id="ycs-channel-input",
                placeholder=(
                    "https://www.youtube.com/@channel, @channel, "
                    "or UCxxxxxxxxxxxxxxxxxxxx"
                ),
                autocomplete="off", cls="ycs-search-input",
            ),
            Input(
                type="number", id="ycs-channel-max",
                value="30", min="0", max="500",
                title="Max videos (0 = all, up to 500; default 30 most recent)",
                cls="ycs-search-max",
            ),
            Button(
                "Find videos", id="ycs-channel-submit",
                type="button", cls="btn-primary",
            ),
            cls="ycs-search-row",
        ),
        cls="ycs-direct-form",
    )


def _SourceTabs():
    """Step-1 Source · 4-mode tab strip + tab bodies.

    All four modes (Search / Videos / Playlist / Channel) feed the same
    results grid and selection cart downstream — they only differ in HOW
    the user specifies which videos to enumerate.
    """
    return Div(
        Div(
            Button(
                "Search", id="ycs-tab-search", type="button",
                data_tab="search", cls="ycs-tab active",
            ),
            Button(
                "Videos", id="ycs-tab-videos", type="button",
                data_tab="videos", cls="ycs-tab",
            ),
            Button(
                "Playlist", id="ycs-tab-playlist", type="button",
                data_tab="playlist", cls="ycs-tab",
            ),
            Button(
                "Channel", id="ycs-tab-channel", type="button",
                data_tab="channel", cls="ycs-tab",
            ),
            cls="ycs-tabs",
        ),
        Div(
            _SearchPanel(),
            id="ycs-tab-body-search", cls="ycs-tab-body active",
        ),
        Div(
            _VideosTabBody(),
            id="ycs-tab-body-videos", cls="ycs-tab-body",
        ),
        Div(
            _PlaylistTabBody(),
            id="ycs-tab-body-playlist", cls="ycs-tab-body",
        ),
        Div(
            _ChannelTabBody(),
            id="ycs-tab-body-channel", cls="ycs-tab-body",
        ),
        cls="ycs-source-tabs",
    )


def _SearchResults():
    return Div(
        Div("", id="ycs-search-status", cls="ycs-search-status"),
        Div("", id="ycs-search-results", cls="ycs-search-results"),
        cls="ycs-search-results-wrap",
    )


def _Cart():
    return Div(
        Span("0 videos staged", id="ycs-cart-count", cls="ycs-cart-count"),
        Button(
            "Continue → Ingest",
            id="ycs-cart-continue",
            type="button",
            cls="btn-primary",
            disabled=True,
            title="The Ingest step lands in the next milestone",
        ),
        id="ycs-cart", cls="ycs-cart",
    )


def _Form():
    return Form(
        Div(
            Span("YouTube video URL", cls="ycs-label"),
            Input(
                type="url",
                name="video_url",
                id="ycs-video-url",
                placeholder="https://www.youtube.com/watch?v=...",
                required=True,
                autocomplete="off",
                cls="ycs-input",
            ),
            cls="ycs-field",
        ),
        Div(
            Span("Your question", cls="ycs-label"),
            Textarea(
                "",
                name="question",
                id="ycs-question",
                placeholder="What is the speaker's main argument?",
                rows="3",
                required=True,
                cls="ycs-input ycs-textarea",
            ),
            cls="ycs-field",
        ),
        Div(
            Button("Ask", type="submit", id="ycs-submit", cls="btn-primary"),
            cls="ycs-actions",
        ),
        id="ycs-form",
        cls="ycs-form",
    )


def _StatusBanner():
    return Div(
        Span("", id="ycs-status-text", cls="ycs-status-text"),
        id="ycs-status",
        cls="ycs-status",
        style="display:none;",
    )


def _Indexed():
    return Div(
        "", id="ycs-indexed", cls="ycs-indexed", style="display:none;",
    )


def _AnswerCard():
    return Div(
        Div("Answer", cls="ycs-eyebrow"),
        Div("", id="ycs-answer-text", cls="ycs-answer-text"),
        Div(
            Span("", id="ycs-answer-meta", cls="ycs-answer-meta"),
            cls="ycs-answer-meta-row",
        ),
        id="ycs-answer",
        cls="ycs-answer",
        style="display:none;",
    )


def _Citations():
    return Div(
        Div("Sources", cls="ycs-eyebrow"),
        Div("", id="ycs-citations-list", cls="ycs-citations-list"),
        id="ycs-citations",
        cls="ycs-citations",
        style="display:none;",
    )


def _Body():
    return Div(
        _Steps(),
        P(
            "Find YouTube videos by topic, channel, duration, date, and more. "
            "Stage the ones worth ingesting; continue to Ingest when ready.",
            cls="ycs-intro",
        ),
        _SourceTabs(),
        _SearchResults(),
        _Cart(),
        # Legacy single-page Q&A still rendered so the working demo path
        # stays usable while the wizard is partially built. Deleted once
        # Steps 2 (Ingest) and 3 (Ask) replace it end-to-end.
        Div(cls="ycs-legacy-divider"),
        P(
            "Legacy single-shot Q&A (paste a URL + a question for an "
            "immediate grounded answer):",
            cls="ycs-intro",
        ),
        _Form(),
        _StatusBanner(),
        _Indexed(),
        _AnswerCard(),
        _Citations(),
        Script(src="/static/js/youtube_content_search.js", defer=True),
        cls="ycs-root",
    )


def register(rt) -> None:
    @rt("/youtube-content-search")
    def youtube_content_search():
        return _Shell(
            "youtube-content-search",
            "YouTube Content Search",
            body=_Body(),
        )
