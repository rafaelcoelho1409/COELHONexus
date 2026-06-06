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
    See docs/UI-ARCHITECTURE-SOTA-2026-05-18.md Day 3 (3-zone layout)."""
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
        Div(
            Div(
                Div("Activity", cls = "fw-node-drawer-section-title"),
                Div(
                    Div("Open a node to stream its events here.",
                        cls = "fw-empty",
                        id = "fw-node-drawer-log-empty"),
                    Div("", id = "fw-node-drawer-log",
                        cls = "fw-node-drawer-log"),
                    cls = "fw-node-drawer-log-wrap",
                ),
                cls = "fw-node-drawer-section",
            ),
            Div("", id = "fw-node-drawer-details",
                cls = "fw-node-drawer-details"),
            id = "fw-node-drawer-body", cls = "fw-drawer-body",
        ),
        id = "fw-node-drawer", cls = "fw-drawer",
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
