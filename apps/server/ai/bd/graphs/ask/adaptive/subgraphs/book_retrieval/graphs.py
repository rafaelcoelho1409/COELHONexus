"""Compile Ask's shared indexed and optional external retrieval workflow."""

from __future__ import annotations

from ...... import nodes
from ..... import book_rag_retrieval
from ... import entities
from . import states


def book_retrieval_graph():
    """Compile Ask's shared indexed and optional external retrieval workflow."""
    return book_rag_retrieval.compile_book_rag_retrieval_graph(
        state_schema=states.BookRetrievalState,
        context_schema=entities.AskContext,
        prepare_node=nodes.ask.adaptive.subgraphs.book_retrieval.prepare.node.prepare_retrieval,
        elasticsearch_node=nodes.ask.adaptive.subgraphs.book_retrieval.elasticsearch.node.retrieve,
        qdrant_node=nodes.ask.adaptive.subgraphs.book_retrieval.qdrant.node.retrieve,
        neo4j_node=nodes.ask.adaptive.subgraphs.book_retrieval.neo4j.node.retrieve,
        web_search_node=nodes.ask.adaptive.subgraphs.book_retrieval.web_search.node.retrieve,
        fuse_node=nodes.ask.adaptive.subgraphs.book_retrieval.fuse.node.fuse_evidence,
    )
