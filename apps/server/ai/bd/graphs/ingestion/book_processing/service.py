"""Public construction API for the Book Distiller ingestion graph."""
from __future__ import annotations
from . import main

from typing import Any


def ingestion_graph(checkpointer: Any | None = None):
    """Compile the root ingestion graph with its owned subgraphs."""
    return main.graphs.ingestion_graph(checkpointer = checkpointer)
