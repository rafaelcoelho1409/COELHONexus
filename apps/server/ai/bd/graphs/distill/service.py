"""Convenience entry point for compiling the Book Distiller graph."""
from __future__ import annotations

from typing import Any

from . import entities, graphs


def distill_graph(dependencies: entities.DistillDependencies, checkpointer: Any | None = None):
    return graphs.distill_graph(dependencies, checkpointer = checkpointer)
