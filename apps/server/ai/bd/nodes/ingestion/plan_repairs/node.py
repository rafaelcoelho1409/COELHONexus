"""Thin LangGraph node for deterministic specialist routing."""
from __future__ import annotations
from . import domain
from .... import graphs


def plan_repairs(graph_state: graphs.ingestion.state.IngestionRunState) -> dict:
    return domain.plan_repairs(graph_state)
