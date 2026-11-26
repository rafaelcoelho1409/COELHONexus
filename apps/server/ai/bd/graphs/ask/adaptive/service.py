"""Public construction entry point for the adaptive Ask graph."""
from __future__ import annotations
from . import main


def adaptive_ask_graph():
    """Compile the root adaptive Ask workflow."""
    return main.graphs.adaptive_ask_graph()
