"""Overlays — modals, drawers, toast. All hidden on first render; JS reveals."""
from fasthtml.common import Button, Div, P, Span


def ConfirmModal():
    """Reused by delete + future destructive actions."""
    return Div(
        Div(
            Div("", id = "fw-modal-title", cls = "fw-modal-title"),
            P("", id = "fw-modal-message", cls = "fw-modal-message"),
            Div(
                Button("Cancel", id = "fw-modal-cancel", cls = "btn-outline"),
                Button("Confirm", id = "fw-modal-confirm", cls = "btn-primary"),
                cls = "fw-modal-actions",
            ),
            cls = "fw-modal",
        ),
        id = "fw-modal", cls = "fw-modal-backdrop",
    )


def FileDrawer():
    """Right-anchored slide-out for viewing individual ingested pages."""
    return Div(
        Div(
            Div(
                Div("", id = "fw-drawer-name", cls = "fw-drawer-name"),
                Div("", id = "fw-drawer-meta", cls = "fw-drawer-meta"),
                cls = "fw-drawer-title",
            ),
            Div(
                Button("◀", id = "fw-drawer-prev",
                       cls = "fw-drawer-btn", title = "Previous (←)"),
                Button("▶", id = "fw-drawer-next",
                       cls = "fw-drawer-btn", title = "Next (→)"),
                Button("✕", id = "fw-drawer-close",
                       cls = "fw-drawer-btn", title = "Close (Esc)"),
                cls = "fw-drawer-controls",
            ),
            cls = "fw-drawer-header",
        ),
        Div("", id = "fw-drawer-body", cls = "fw-drawer-body"),
        id = "fw-drawer", cls = "fw-drawer",
    )


def NodeDrawer():
    """Right-anchored slide-out for planner/synth node inspection.

    SOTA layout (2026-06-08 redesign per LangSmith / Dagster / Vercel /
    Langfuse step-detail panes): sticky header (icon + title + meta +
    close) → sticky KPI strip → tab strip → tab content area.

    Tabs:
      Overview  — rich SUBSTEP_RENDERERS output (KPI cards, tables,
                  outline, metadata footer). Default.
      Activity  — live SSE event stream with severity + "new since
                  last viewed" highlights. Badge shows new-event count.
      Raw       — inputs + outputs JSON accordions (advanced).

    All three tab containers always exist in the DOM so the JS only
    flips `display:none` instead of innerHTML-thrashing on every
    selection — cheaper and lets users alternate tabs without
    re-rendering."""
    return Div(
        Div(
            Div(
                Span("○", id = "fw-node-drawer-icon",
                     cls = "fw-node-drawer-icon"),
                Div(
                    Div("", id = "fw-node-drawer-title",
                        cls = "fw-drawer-name"),
                    Div("", id = "fw-node-drawer-meta",
                        cls = "fw-drawer-meta"),
                    cls = "fw-drawer-title",
                ),
                cls = "fw-node-drawer-head-left",
            ),
            Div(
                Button("✕", id = "fw-node-drawer-close",
                       cls = "fw-drawer-btn", title = "Close (Esc)"),
                cls = "fw-drawer-controls",
            ),
            cls = "fw-drawer-header",
        ),
        Div("", id = "fw-node-drawer-kpis", cls = "fw-node-drawer-kpis"),
        # Tab strip — three named tabs. The active class is toggled
        # by drawer.js; CSS handles the underline + color.
        Div(
            Button(
                "Overview",
                id = "fw-node-drawer-tab-overview",
                cls = "fw-node-drawer-tab active",
                data_tab = "overview",
                type = "button",
            ),
            Button(
                Span("Activity"),
                Span("", id = "fw-node-drawer-tab-activity-badge",
                     cls = "fw-node-drawer-tab-badge"),
                id = "fw-node-drawer-tab-activity",
                cls = "fw-node-drawer-tab",
                data_tab = "activity",
                type = "button",
            ),
            Button(
                "Raw I/O",
                id = "fw-node-drawer-tab-raw",
                cls = "fw-node-drawer-tab",
                data_tab = "raw",
                type = "button",
            ),
            cls = "fw-node-drawer-tabs",
            id = "fw-node-drawer-tabs",
        ),
        Div(
            # Overview tab — rich renderer output.
            Div(
                Div("", id = "fw-node-drawer-details",
                    cls = "fw-node-drawer-details"),
                cls = "fw-node-drawer-tab-panel active",
                data_tab = "overview",
                id = "fw-node-drawer-tab-overview-panel",
            ),
            # Activity tab — live event log.
            Div(
                Div(
                    Div("Open a node to stream its events here.",
                        cls = "fw-empty",
                        id = "fw-node-drawer-log-empty"),
                    Div("", id = "fw-node-drawer-log",
                        cls = "fw-node-drawer-log"),
                    cls = "fw-node-drawer-log-wrap",
                ),
                cls = "fw-node-drawer-tab-panel",
                data_tab = "activity",
                id = "fw-node-drawer-tab-activity-panel",
            ),
            # Raw I/O tab — inputs + outputs accordions.
            Div(
                Div("", id = "fw-node-drawer-raw",
                    cls = "fw-node-drawer-raw"),
                cls = "fw-node-drawer-tab-panel",
                data_tab = "raw",
                id = "fw-node-drawer-tab-raw-panel",
            ),
            id = "fw-node-drawer-body", cls = "fw-drawer-body",
        ),
        id = "fw-node-drawer", cls = "fw-drawer",
    )


def LlmUsageDrawer():
    """Right-anchored slide-out for pipeline-level LLM usage summaries."""
    return Div(
        Div(
            Div(
                Div("LLM usage", id = "fw-llm-drawer-name",
                    cls = "fw-drawer-name"),
                Div("", id = "fw-llm-drawer-meta", cls = "fw-drawer-meta"),
                cls = "fw-drawer-title",
            ),
            Div(
                Button("✕", id = "fw-llm-drawer-close",
                       cls = "fw-drawer-btn", title = "Close (Esc)"),
                cls = "fw-drawer-controls",
            ),
            cls = "fw-drawer-header",
        ),
        Div(
            Div(
                Div("Planner LLM usage", cls = "dd-llm-rail-label"),
                Div(id = "fw-planner-llm-totals", cls = "dd-llm-rail-host"),
                id = "fw-llm-drawer-planner-section",
                cls = "dd-llm-rail-section",
            ),
            Div(
                Div("Synth chapter usage", cls = "dd-llm-rail-label"),
                Div(id = "fw-synth-llm-chapters", cls = "dd-llm-rail-host"),
                id = "fw-llm-drawer-synth-chapters-section",
                cls = "dd-llm-rail-section",
            ),
            Div(
                Div("Synth total", cls = "dd-llm-rail-label"),
                Div(id = "fw-synth-llm-total", cls = "dd-llm-rail-host"),
                id = "fw-llm-drawer-synth-total-section",
                cls = "dd-llm-rail-section",
            ),
            id = "fw-llm-drawer-body", cls = "fw-drawer-body",
        ),
        id = "fw-llm-drawer", cls = "fw-drawer",
    )


def NoticeAndToast():
    """Cache notice + denied toast — both start hidden, JS toggles."""
    return (
        Div(
            Span("", id = "fw-cache-notice-text", cls = "fw-notice-text"),
            id = "fw-cache-notice", cls = "fw-notice", style = "display:none;",
        ),
        Div(
            Span("", id = "fw-denied-toast-text", cls = "fw-toast-text"),
            Button("✕", id = "fw-denied-toast-close", cls = "fw-toast-close"),
            id = "fw-denied-toast", cls = "fw-toast", style = "display:none;",
        ),
    )
