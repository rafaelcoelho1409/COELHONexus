"""
KDInspect — Knowledge Distiller markdown quality inspector.

Three-pane layout (frameworks · files · preview) wired entirely with HTMX
fragments served by the FastAPI inspect router. Server-side markdown
rendering keeps the page dependency-free; htmx swaps replace inner HTML
of each pane on click.

Direct port of apps/web/main.go's inline `kdInspectPage` + the planned
templates/kd_inspect.templ. Frameworks rail loads on page load; clicking
a framework populates the file list rail; clicking a file populates the
preview pane.
"""
from fasthtml.common import (
    Aside, Div, H2, I, Input, Main, Nav, Script, Section,
)

from components.base import Page


# Inline JS for the case-insensitive file-list filter (matches the inline
# script from apps/web/main.go's kdInspectPage). Defined here so the only
# script the page ships beyond Page()'s footer is this one filter helper.
_FILTER_SCRIPT = """
    // Case-insensitive substring filter for the file list.
    window.kdFilterFiles = (q) => {
      const re = q ? new RegExp(q.replace(/[.*+?^${}()|[\\]\\\\]/g, "\\\\$&"), "i") : null;
      document.querySelectorAll("#kd-file-list [data-file-row]").forEach((row) => {
        row.style.display = !re || re.test(row.dataset.fileRow) ? "" : "none";
      });
    };
"""


def _FrameworksRail():
    """Left rail — list of frameworks with cached corpora in MinIO."""
    return Aside(
        Div(
            H2(
                "Frameworks",
                cls="text-[0.7rem] font-semibold uppercase tracking-wider text-base-content/60",
            ),
            Div("Ingested into MinIO",
                cls="text-[0.65rem] text-base-content/50 mt-0.5"),
            cls="px-4 py-3 border-b border-base-300 sticky top-0 bg-base-100 z-10",
        ),
        Nav(
            Div("Loading…", cls="text-xs text-base-content/50 px-3 py-2"),
            id="kd-framework-list",
            hx_get="/api/kd/inspect/frameworks",
            hx_trigger="load",
            hx_swap="innerHTML",
            cls="p-2 flex flex-col gap-0.5",
        ),
        cls="w-56 shrink-0 border-r border-base-300 overflow-y-auto bg-base-100",
    )


def _FileListRail():
    """Middle rail — files for the selected framework + filter input."""
    return Aside(
        Div(
            H2(
                "Files",
                id="kd-file-pane-title",
                cls=(
                    "text-[0.7rem] font-semibold uppercase tracking-wider "
                    "text-base-content/60 truncate flex-1"
                ),
            ),
            Input(
                type="search",
                placeholder="filter…",
                cls="input input-xs input-bordered w-28 text-xs",
                oninput="kdFilterFiles(this.value)",
            ),
            cls=(
                "px-4 py-3 border-b border-base-300 sticky top-0 bg-base-100 z-10 "
                "flex items-center gap-2"
            ),
        ),
        Div(
            Div("Pick a framework on the left.",
                cls="text-xs text-base-content/50 px-3 py-4"),
            id="kd-file-list",
            cls="p-2 flex flex-col gap-0.5",
        ),
        id="kd-file-pane",
        cls="w-80 shrink-0 border-r border-base-300 overflow-y-auto bg-base-100",
    )


def _PreviewPane():
    """Right pane — rendered markdown preview for the selected file."""
    return Section(
        Div(
            Div(
                I(data_lucide="file-search", cls="w-12 h-12 mx-auto mb-3 opacity-40"),
                Div("Select a file to preview its rendered markdown.",
                    cls="text-sm"),
                Div("Quality stats appear above the rendered output.",
                    cls="text-xs mt-1 opacity-70"),
                cls="text-base-content/50 text-center py-24",
            ),
            id="kd-preview",
            cls="max-w-4xl mx-auto px-8 py-8",
        ),
        id="kd-preview-pane",
        cls="flex-1 overflow-y-auto bg-base-200 min-w-0",
    )


def KDInspectPage():
    """Top-level KD inspector page (matches apps/web/main.go::kdInspectHandler)."""
    return Page(
        "Inspect Markdown · KD",
        # The 3-pane layout extends edge-to-edge under the sidebar's 64-unit
        # margin. Use negative left margin to claw back the space and full
        # screen height for the panes.
        Div(
            _FrameworksRail(),
            _FileListRail(),
            _PreviewPane(),
            cls="flex h-screen -ml-64 pl-64",
        ),
        Script(_FILTER_SCRIPT),
        active_nav="kd-inspect",
    )
