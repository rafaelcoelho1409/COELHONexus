"""Row-3 toolbar widgets for Step 4 · Query.

Single pill strip — `QueryBackendTabs` (Elasticsearch | Qdrant | Neo4j).
The app pivot (DD/YCS/RR) was removed 2026-06-15 — the page is scoped
to YCS while DD + RR have nothing meaningful to browse here. When that
changes, re-introduce a `QueryAppTabs` widget alongside this one.

`query.js` reads `data-query-backend` on click and swaps the active
panel + re-runs the search. The aria-pressed flag is toggled in JS so
the server-rendered initial state matches whatever the user lands on
(default: backend = elasticsearch)."""
from __future__ import annotations

from fasthtml.common import Button, Div


_BACKENDS: tuple[tuple[str, str], ...] = (
    ("elasticsearch", "Elasticsearch"),
    ("qdrant",        "Qdrant"),
    ("neo4j",         "Neo4j"),
)


def QueryBackendTabs(active: str = "elasticsearch"):
    """Three-pill backend selector. Visual language matches
    `SourceModeTabs` / `AskModeTabs` (`.dd-substage-nav` + `.dd-substage`)
    so the topbar reads as one chrome family across YCS stages."""
    pills = [
        Button(
            label,
            type            = "button",
            cls             = "dd-substage active" if key == active else "dd-substage",
            data_query_backend = key,
            aria_pressed    = "true" if key == active else "false",
        )
        for key, label in _BACKENDS
    ]
    return Div(
        *pills,
        cls = "dd-substage-nav ycs-query-backend-tabs",
        role = "tablist",
        aria_label = "Query backend",
    )
