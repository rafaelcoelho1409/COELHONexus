"""Compile the parallel book retrieval and evidence fusion workflow."""
from __future__ import annotations
from ... import entities, states
from ..... import book_rag_retrieval
from ...... import nodes


def book_retrieval_graph():
    """Compile Distill's shared indexed and optional external retrieval workflow."""
    return book_rag_retrieval.compile_book_rag_retrieval_graph(
        state_schema = states.DistillRunState,
        context_schema = entities.DistillDependencies,
        prepare_node = nodes.distill.book_distillation.subgraphs.book_retrieval.prepare.node.prepare_retrieval,
        elasticsearch_node = nodes.distill.book_distillation.subgraphs.book_retrieval.elasticsearch.node.elasticsearch_retrieval,
        qdrant_node = nodes.distill.book_distillation.subgraphs.book_retrieval.qdrant.node.qdrant_retrieval,
        neo4j_node = nodes.distill.book_distillation.subgraphs.book_retrieval.neo4j.node.neo4j_retrieval,
        web_search_node = nodes.distill.book_distillation.subgraphs.book_retrieval.web_search.node.web_search_retrieval,
        fuse_node = nodes.distill.book_distillation.subgraphs.book_retrieval.fuse.node.fuse_evidence,
    )
