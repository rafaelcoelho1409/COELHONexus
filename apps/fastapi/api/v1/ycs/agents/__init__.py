"""ycs/agents — agentic RAG router (Adaptive STANDARD/FAST/DEEP).

Direct port of deprecated `routers/v1/youtube/agents.py`.

Public:
  router — APIRouter aggregating PUT /config, POST /search, /search/stream
           SSE, POST /ingest/qdrant, /ingest/neo4j, GET /graph/stats,
           POST /pipeline"""
from .router import router


__all__ = ["router"]
