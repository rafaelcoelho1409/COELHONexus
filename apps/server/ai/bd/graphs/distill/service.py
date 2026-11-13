"""Convenience entry point for compiling the Book Distiller graph."""
from __future__ import annotations
from . import entities, graphs

from typing import Any


def distill_graph(dependencies: entities.DistillDependencies, checkpointer: Any | None = None):
    return graphs.distill_graph(dependencies, checkpointer = checkpointer)
