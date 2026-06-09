"""Docs Distiller stage URL table + builder.

`StageSubNav` reads `_STAGES` to render the row-2 tab strip.
`stage_url(stage, slug)` centralizes the `?slug=` concatenation so every
caller agrees on the convention (catalog is the picker → never carries
a slug; every other stage carries the active one when present)."""


_STAGES = [
    ("catalog",   "Catalog",   "/docs-distiller"),
    ("ingestion", "Ingestion", "/docs-distiller/ingestion"),
    ("pipeline",  "Pipeline",  "/docs-distiller/pipeline"),
    ("study",     "Study",     "/docs-distiller/study"),
]


def stage_url(stage_key: str, slug: str | None) -> str:
    base = next(
        (href for key, _, href in _STAGES if key == stage_key),
        "/docs-distiller",
    )
    if stage_key != "catalog" and slug:
        return f"{base}?slug={slug}"
    return base
