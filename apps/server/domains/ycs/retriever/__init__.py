"""ycs/retriever — ES + Qdrant hybrid + Neo4j + SmartRetriever orchestrator.
All four retrievers share the same `retrieve(query, channel_ids)`
interface so the SmartRetriever fans out uniformly."""
from __future__ import annotations
from . import domain, params, prompts, schemas, service
