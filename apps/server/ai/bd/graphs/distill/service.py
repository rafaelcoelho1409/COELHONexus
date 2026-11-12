"""Convenience entry point for compiling the Book Distiller graph."""
from __future__ import annotations

import typing

from . import entities, graphs


def build_graph(dependencies: entities.DistillDependencies, checkpointer: typing.Any | None = None):
    return graphs.distill_graph(dependencies, checkpointer = checkpointer)
