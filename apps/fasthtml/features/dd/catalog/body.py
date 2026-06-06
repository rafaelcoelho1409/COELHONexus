"""Catalog body — framework tile grid."""
from fasthtml.common import Div, P

from .widgets import _tile


def CatalogBody(catalog: list[dict]):
    if not catalog:
        return Div(
            P(
                "Could not load the framework catalog. "
                "Make sure FastAPI is reachable at /api/v1/docs-distiller/resolver.",
                cls = "fw-empty",
            ),
            cls = "fw-step-panel active",
            id = "fw-step-1-panel",
        )
    tiles = [_tile(f) for f in catalog]
    # Search + count + category filter moved to the row-3 toolbar.
    # The body is now just the tile grid.
    return Div(
        Div(
            Div(*tiles, cls = "fw-grid", id = "fw-grid"),
            id = "fw-step-1-edit",
        ),
        cls = "fw-step-panel active",
        id = "fw-step-1-panel",
    )
