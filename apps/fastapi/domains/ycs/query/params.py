"""ycs/query — limits, app/backend identifiers, app→namespace map.

Per docs/CODE-CONVENTIONS.md §2 — loose scalars + identifier constants
live here, not in `service.py` (where the I/O orchestration lives).

The `APP_BACKENDS` map is the single source of truth for which (app,
backend) pairs are queryable. Today: DD has no presence in ES / Qdrant /
Neo4j (writes only to MinIO + Postgres), so DD is registered as
unsupported in every backend — endpoints return an empty result + the
unsupported flag rather than 404, so the UI can keep the tab visible
and grey-out the chip."""
from __future__ import annotations

from dataclasses import dataclass


# Result-set sizing. `MAX_LIMIT` is the hard ceiling — clamps any
# client-supplied `limit` so a stray `?limit=10000` can't fan out into a
# multi-megabyte ES + Qdrant + Neo4j payload.
DEFAULT_LIMIT: int = 20
MAX_LIMIT:     int = 100

# Per-hit text snippet cap. The raw `Document.text` / transcript `content`
# fields can be tens of kilobytes; we truncate at projection time so the
# JSON response stays bounded.
SNIPPET_CHARS: int = 600


# Identifier strings — kept here, not as inlined literals scattered
# through schemas / service / router. Rename in one place → propagates.
APP_DD:  str = "dd"
APP_YCS: str = "ycs"
APP_RR:  str = "rr"

BACKEND_ES:     str = "elasticsearch"
BACKEND_QDRANT: str = "qdrant"
BACKEND_NEO4J:  str = "neo4j"

APPS:     tuple[str, ...] = (APP_DD, APP_YCS, APP_RR)
BACKENDS: tuple[str, ...] = (BACKEND_ES, BACKEND_QDRANT, BACKEND_NEO4J)


@dataclass(frozen=True, slots=True)
class AppNamespace:
    """What an app owns inside ONE backend.

    `available` False = the app has no presence in that backend (DD is
    this everywhere today). The UI greys out the chip and the service
    short-circuits to an empty response. Keeping the entry (vs deleting)
    lets the frontend render a uniform 3x3 grid + a clear "no data"
    explanation."""
    available: bool
    # Human-readable namespace label for the response (and the UI's
    # "Searching in: …" caption). Empty when unavailable.
    label:     str = ""
    # The actual store-side identifier:
    #   - ES:     comma-joined index names ("idx_a,idx_b")
    #   - Qdrant: collection name
    #   - Neo4j:  comma-joined node labels searched
    target:    str = ""


# Source of truth — the 3x3 (app x backend) matrix. Service + router
# both read from here so adding RR-to-ES later is a one-line change.
APP_BACKENDS: dict[str, dict[str, AppNamespace]] = {
    APP_DD: {
        BACKEND_ES:     AppNamespace(available = False),
        BACKEND_QDRANT: AppNamespace(available = False),
        BACKEND_NEO4J:  AppNamespace(available = False),
    },
    APP_YCS: {
        BACKEND_ES: AppNamespace(
            available = True,
            label     = "YCS · metadata + transcripts",
            target    = "coelhonexus-youtube-metadata,coelhonexus-youtube-transcriptions",
        ),
        BACKEND_QDRANT: AppNamespace(
            available = True,
            label     = "YCS · transcript chunks",
            target    = "youtube-transcripts",
        ),
        BACKEND_NEO4J: AppNamespace(
            available = True,
            label     = "YCS · entities + videos",
            target    = "__Entity__,Document,Video,Channel",
        ),
    },
    APP_RR: {
        BACKEND_ES:     AppNamespace(available = False),
        BACKEND_QDRANT: AppNamespace(
            available = True,
            label     = "RR · paper abstracts",
            target    = "radar_papers",
        ),
        BACKEND_NEO4J: AppNamespace(
            available = True,
            label     = "RR · papers + authors + concepts",
            target    = "Paper,Author,Concept,Source",
        ),
    },
}


def is_supported(app: str, backend: str) -> bool:
    """True when the (app, backend) pair has data we can query."""
    return APP_BACKENDS.get(app, {}).get(backend, AppNamespace(False)).available


def namespace_label(app: str, backend: str) -> str:
    """Human-readable label for the (app, backend) target — used in the
    response's `namespace` field. Empty when unsupported."""
    return APP_BACKENDS.get(app, {}).get(backend, AppNamespace(False)).label
