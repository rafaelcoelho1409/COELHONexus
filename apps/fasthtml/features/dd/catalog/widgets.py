"""Catalog widgets — small reusable view primitives.

`_tile` is consumed by `CatalogBody` to render each framework. Multi-logo
stack entries (LangChain bundle, Grafana bundle) render every component
logo in a horizontal strip; single-logo entries fall back to one image."""
from fasthtml.common import Div, Img, Span


def _tile(f: dict):
    children = []
    logos = f.get("logos") or []
    if logos:
        children.append(Div(
            *[Img(src = u, alt = "", cls = "fw-tile-logo-multi") for u in logos],
            cls = "fw-tile-logos",
        ))
    elif f.get("logo"):
        children.append(Img(src = f["logo"], alt = "", cls = "fw-tile-logo"))
    children.append(Div(f["name"], cls = "fw-tile-name"))
    children.append(Div(f.get("category") or "—", cls = "fw-tile-cat"))
    # Revealed by CSS only when picker.js adds `.fw-tile-ingested` (slug
    # found in /ingestion library). Shows which catalog frameworks have
    # already been downloaded.
    children.append(Span("✓ Ingested", cls = "fw-tile-badge", aria_hidden = "true"))
    return Div(
        *children,
        cls = "fw-tile",
        data_name = f["name"],
        data_slug = f["slug"],
        data_category = (f.get("category") or "Other"),
    )
