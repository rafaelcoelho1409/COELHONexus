"""Compile the parallel source-specialist BD retrieval graph."""
from __future__ import annotations
from . import entities, states

from functools import lru_cache
from langgraph.graph import END, START, StateGraph


@lru_cache(maxsize = 1)
def retrieval_graph():
    """Return four isolated specialist nodes followed by deterministic fusion."""
    from .... import nodes

    source_nodes = (
        ("elasticsearch", nodes.rag.elasticsearch.retrieval.node.retrieve),
        ("qdrant", nodes.rag.qdrant.retrieval.node.retrieve),
        ("neo4j", nodes.rag.neo4j.retrieval.node.retrieve),
        ("web_search", nodes.rag.web_search.retrieval.node.retrieve),
    )
    workflow = StateGraph(states.RetrievalGraphState, context_schema = entities.RetrievalContext)
    for name, node in source_nodes:
        workflow.add_node(name, node)
        workflow.add_edge(START, name)
    workflow.add_node("fuse_evidence", nodes.rag.fuse.node.fuse_evidence)
    workflow.add_edge([name for name, _ in source_nodes], "fuse_evidence")
    workflow.add_edge("fuse_evidence", END)
    return workflow.compile(name = "bd_source_specialist_retrieval")
