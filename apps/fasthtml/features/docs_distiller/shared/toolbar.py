"""Row 3 — contextual stage tools (left) + framework picker (right).

Left content branches per stage. Catalog drops the framework picker (its
grid IS the framework list; ingested ones are green-badged inline)."""
from fasthtml.common import Div

from ..catalog.chrome import CatalogSearch, CategoryFilter
from ..planner.chrome import PlannerActions, PlannerPill
from ..study.chrome import StudyTabs, StudyViewButtons
from ..synth.chrome import SynthActions, SynthPill
from .picker import FrameworkPicker


def StageToolbar(active_stage: str, slug: str | None,
                 catalog: list[dict] | None = None):
    if active_stage == "catalog":
        left = [CatalogSearch(catalog), CategoryFilter(catalog)]
    elif active_stage == "planner":
        left = [PlannerPill(), PlannerActions()]
    elif active_stage == "synth":
        left = [SynthPill(), SynthActions()]
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
