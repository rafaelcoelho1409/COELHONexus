"""
KD MAP A/B compare page — visual UI for `/api/v1/knowledge/debug/map_compare`.

Form lets the user pick framework + shard config + which paths to run.
On submit, HTMX posts to /kd/map-compare/run which hits the FastAPI JSON
endpoint via the shared reverse-proxy and renders side-by-side per-shard
cluster output (cluster names + file counts + cluster sizes).

Defaults to classical_only=true because the LLM-side path cascades
through 37 frontier models on each shard and routinely takes minutes;
classical-only finishes in <30s and matches the production planner path.
"""
from fasthtml.common import (
    Button, Div, Form, H1, H2, H3, I, Input, Label, P, Section, Span, Table, Tbody, Td, Th, Thead, Tr,
)

from components.base import Page


def _Form():
    """Form posts to /kd/map-compare/run via HTMX; results swap into #map-compare-result."""
    return Form(
        Div(
            Label("Study root", cls="text-xs font-semibold text-base-content/70"),
            Input(
                name="study_root",
                type="text",
                placeholder="default/knowledge/terragrunt-latest",
                value="default/knowledge/terragrunt-latest",
                cls="input input-sm input-bordered w-full",
                required=True,
            ),
            cls="flex flex-col gap-1",
        ),
        Div(
            Label("Framework", cls="text-xs font-semibold text-base-content/70"),
            Input(
                name="framework",
                type="text",
                placeholder="Terragrunt",
                value="Terragrunt",
                cls="input input-sm input-bordered w-full",
                required=True,
            ),
            cls="flex flex-col gap-1",
        ),
        Div(
            Label("Shard size", cls="text-xs font-semibold text-base-content/70"),
            Input(
                name="shard_size",
                type="number",
                value="40",
                min="5",
                max="100",
                cls="input input-sm input-bordered w-32",
            ),
            cls="flex flex-col gap-1",
        ),
        Div(
            Label("Max shards (optional)", cls="text-xs font-semibold text-base-content/70"),
            Input(
                name="max_shards",
                type="number",
                placeholder="all",
                min="1",
                cls="input input-sm input-bordered w-32",
            ),
            cls="flex flex-col gap-1",
        ),
        # Boolean toggles — DaisyUI checkboxes
        Div(
            Label(
                Input(
                    type="checkbox",
                    name="skip_off_topic_filter",
                    value="true",
                    cls="checkbox checkbox-sm",
                ),
                Span("Skip off-topic filter (faster; no semantic noise drop)",
                     cls="text-xs text-base-content/80"),
                cls="cursor-pointer flex items-center gap-2",
            ),
            cls="flex flex-col gap-1",
        ),
        Div(
            Label(
                Input(
                    type="checkbox",
                    name="classical_only",
                    value="true",
                    checked=True,
                    cls="checkbox checkbox-sm",
                ),
                Span("Classical only (skip LLM rotator path — recommended)",
                     cls="text-xs text-base-content/80"),
                cls="cursor-pointer flex items-center gap-2",
            ),
            cls="flex flex-col gap-1",
        ),
        Button(
            I(data_lucide="play", cls="w-4 h-4"),
            "Run MAP compare",
            type="submit",
            cls="btn btn-sm btn-primary gap-2 mt-2",
        ),
        # HTMX wiring. `hx-indicator` points at the sibling spinner div which
        # gets `display: flex` while the form has `htmx-request` class.
        # `hx-disabled-elt` only disables the submit button (not the whole
        # form) so users can still see what they typed during a long request.
        hx_post="/kd/map-compare/run",
        hx_target="#map-compare-result",
        hx_swap="innerHTML",
        hx_indicator="#map-compare-spinner",
        hx_disabled_elt="button[type=submit]",
        cls="memo-card flex flex-col gap-3 max-w-xl",
    )


def _Spinner():
    """HTMX-aware spinner shown during long requests.
    `htmx-indicator` class hides the element by default (see base.py CSS);
    HTMX adds `htmx-request` to ancestor on submit → CSS toggle reveals it."""
    return Div(
        I(data_lucide="loader-circle", cls="w-4 h-4 animate-spin"),
        Span("Running MAP — embedding + clustering + KeyLLM (NIM rotator)…",
             cls="text-xs text-base-content/70"),
        id="map-compare-spinner",
        cls="htmx-indicator items-center gap-2 text-sm mt-2",
    )


def MapComparePage():
    """Top-level page — form on left, results render below."""
    return Page(
        "MAP A/B Compare",
        Div(
            # Header
            Div(
                H1("MAP A/B Compare", cls="text-2xl font-bold tracking-tight"),
                P(
                    "Run the planner's MAP step (embed → community_detection → KeyLLM) "
                    "against a cached corpus and inspect per-shard cluster output. "
                    "Defaults to classical-only path; toggle off to also run the legacy "
                    "LLM rotator path (warning: can stall for minutes on rate-limit cascades).",
                    cls="text-sm text-base-content/60 mt-1",
                ),
                cls="mb-6",
            ),
            # Form + spinner
            _Form(),
            _Spinner(),
            # Result swap target
            Section(
                Div(
                    "Submit the form above to run a comparison.",
                    cls="text-sm text-base-content/50",
                ),
                id="map-compare-result",
                cls="mt-6",
            ),
            cls="max-w-5xl mx-auto px-8 py-10",
        ),
        active_nav="kd-map-compare",
    )


# =============================================================================
# Result rendering — called from the route handler with the FastAPI JSON body
# =============================================================================
def MapCompareResult(payload: dict) -> Div:
    """
    Render the JSON returned by FastAPI's /api/v1/knowledge/debug/map_compare.
    `payload` is the parsed dict (not the raw response object).

    Top: summary stats (n_files_initial, dropped, n_shards).
    Body: one section per shard with two columns (LLM | Classical) showing
    each cluster's name + file count.
    """
    summary = Div(
        Span(f"initial={payload.get('n_files_initial', '?')}",
             cls="badge badge-ghost text-xs"),
        Span(f"after off-topic filter={payload.get('n_files_after_off_topic_filter', '?')}",
             cls="badge badge-ghost text-xs"),
        Span(f"off-topic dropped={payload.get('off_topic_dropped', '?')}",
             cls="badge badge-warning text-xs"),
        Span(f"after dedup={payload.get('n_files_after_dedup', '?')}",
             cls="badge badge-ghost text-xs"),
        Span(f"shards={payload.get('n_shards', '?')}",
             cls="badge badge-primary text-xs"),
        cls="flex flex-wrap gap-2 mb-4",
    )

    shards_blocks = []
    for s in payload.get("shards", []):
        shards_blocks.append(_ShardBlock(s))

    if not shards_blocks:
        shards_blocks = [Div("No shards returned.",
                             cls="text-sm text-base-content/50")]

    return Div(
        H2(f"Run on {payload.get('framework', '?')}",
           cls="text-lg font-semibold mb-1"),
        Div(f"study_root: {payload.get('study_root', '?')}",
            cls="text-xs text-base-content/60 font-mono mb-3"),
        summary,
        *shards_blocks,
    )


def _ShardBlock(shard: dict):
    """One side-by-side row: LLM clusters | Classical clusters."""
    idx = shard.get("shard_idx", "?")
    n_files = shard.get("n_files", "?")
    return Div(
        H3(f"Shard {idx} · {n_files} files",
           cls="text-sm font-semibold mb-2 mt-4"),
        Div(
            _PathColumn("LLM", shard.get("llm", {})),
            _PathColumn("Classical", shard.get("classical", {})),
            cls="grid grid-cols-1 md:grid-cols-2 gap-3",
        ),
        cls="memo-card",
    )


def _PathColumn(label: str, path_data: dict):
    """One column showing wall time, error if any, and the cluster table."""
    wall = path_data.get("wall_s", 0.0)
    skipped = path_data.get("skipped", False)
    err = path_data.get("error")
    clusters = path_data.get("clusters", [])
    unused = path_data.get("unused_shard_slugs", [])

    header = Div(
        Span(label, cls="font-semibold text-sm"),
        Span(f"wall={wall}s",
             cls="text-xs text-base-content/60"),
        cls="flex justify-between items-center mb-2",
    )

    if skipped:
        return Div(
            header,
            Div("Skipped (classical_only=true)",
                cls="text-xs text-base-content/50 italic"),
            cls="border border-base-300 rounded-md p-3 bg-base-100",
        )

    if err:
        return Div(
            header,
            Div(f"Error: {err}",
                cls="text-xs text-error font-mono break-all"),
            cls="border border-error/40 rounded-md p-3 bg-base-100",
        )

    rows = [
        Tr(
            Td(c.get("cluster_name", "?"),
               cls="font-mono text-xs whitespace-nowrap"),
            Td(str(len(c.get("file_slugs", []))),
               cls="text-right text-xs tabular-nums"),
        )
        for c in clusters
    ]

    table = Table(
        Thead(
            Tr(
                Th("cluster_name", cls="text-xs uppercase tracking-wider text-left"),
                Th("files", cls="text-xs uppercase tracking-wider text-right"),
            ),
        ),
        Tbody(*rows) if rows else Tbody(
            Tr(Td("(no clusters)", colspan="2",
                  cls="text-xs text-base-content/50 italic")),
        ),
        cls="table table-xs w-full",
    )

    footer = Div(
        Span(f"clusters={len(clusters)}", cls="badge badge-ghost text-xs"),
        Span(f"unused={len(unused)}", cls="badge badge-ghost text-xs"),
        cls="flex gap-2 mt-2",
    )

    return Div(
        header,
        table,
        footer,
        cls="border border-base-300 rounded-md p-3 bg-base-100",
    )
