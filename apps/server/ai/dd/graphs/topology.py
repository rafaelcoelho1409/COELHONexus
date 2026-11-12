"""Read the topology Mermaid source from a compiled Docs Distiller LangGraph."""
from __future__ import annotations

import typing


def mermaid_source(compiled_graph: typing.Any) -> str:
    """Render the graph's compiled topology, including nested subgraphs, without invoking its nodes."""
    return compiled_graph.get_graph(xray = True).draw_mermaid()
