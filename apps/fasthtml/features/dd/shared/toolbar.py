"""Row 3 — contextual stage tools (left) + framework picker (right).

Left content branches per stage. Catalog drops the framework picker (its
grid IS the framework list; ingested ones are green-badged inline)."""
from fasthtml.common import Div

from ..catalog.chrome import CatalogSearch, CategoryFilter
from ..pipeline.chrome import PipelineActions
from ..planner.chrome import PlannerActions, PlannerPill
from ..study.chrome import StudyTabs, StudyViewButtons
from ..synth.chrome import SynthActions, SynthPill
from .picker import FrameworkPicker


def StageToolbar(active_stage: str, slug: str | None,
                 catalog: list[dict] | None = None):
    if active_stage == "catalog":
        left = [CatalogSearch(catalog), CategoryFilter(catalog)]
    elif active_stage == "ingestion":
        # Row 3 summary line for Ingestion (2026-06-08): the manifest
        # render writes `#fw-step2-summary` (same element ID as before
        # — the body version is removed), so JS in
        # `manifest.js:_renderSummary` keeps targeting the same selector.
        # Empty by default; populated once `loadManifestForSlug` resolves.
        left = [Div("", id = "fw-step2-summary",
                    cls = "fw-explorer-summary")]
    elif active_stage == "planner":
        left = [PlannerActions(), PlannerPill()]
    elif active_stage == "synth":
        # Pill on the RIGHT of Wipe/Stop buttons (changed 2026-06-07).
        # Old-commit order was [Pill, Actions]; the new layout puts the
        # pill after the actions so the running-chapter status reads
        # next to where the user's eye lands after clicking Start.
        left = [SynthActions(), SynthPill()]
    elif active_stage == "pipeline":
        # Unified Planner + Synth page (2026-06-08). Per-stage controls
        # stay separate; PipelineActions packs both clusters side-by-side
        # with a → arrow indicating Planner-then-Synth flow.
        left = [PipelineActions()]
    elif active_stage == "study":
        left = [StudyTabs()]
    else:
        left = []
    children = [Div(*left, cls = "dd-toolbar-left")]
    if active_stage != "catalog":
        right = list(StudyViewButtons()) if active_stage == "study" else []
        right.append(FrameworkPicker(slug, catalog))
        children.append(Div(*right, cls = "dd-toolbar-right"))
    return Div(
        *children,
        cls = "dd-toolbar topbar-collapsible",
        id = "dd-toolbar",
    )
