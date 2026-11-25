"""Public construction API for the Book Distiller ingestion graph."""
from __future__ import annotations

from typing import Any


def ingestion_graph(checkpointer: Any | None = None):
    """Compile the root ingestion graph with its owned subgraphs."""
    from .main import graphs

    return graphs.ingestion_graph(checkpointer = checkpointer)
