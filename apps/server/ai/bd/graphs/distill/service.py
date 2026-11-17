"""Convenience entry point for compiling the Book Distiller graph."""
from __future__ import annotations
from . import graphs

from typing import Any


def distill_graph(checkpointer: Any | None = None):
    return graphs.distill_graph(checkpointer = checkpointer)
