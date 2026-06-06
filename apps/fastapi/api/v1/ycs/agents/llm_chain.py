"""ycs/agents — 13-model `with_fallbacks` LLM chain factory.

Direct port of deprecated `tasks/youtube/neo4j.py:L62-103` + the deprecated
`app.py` provisioning pattern (`request.app.state.llm`). Used by:
  - SmartRetriever's Neo4jRetriever (entity extraction in queries)
  - DocumentGrader (relevance grading)
  - The adaptive RAG graph nodes (classify, generate, contextualize, …)

Same Groq (speed) → NVIDIA NIM (capacity) fallback list as the Neo4j
ingestion task. Bundled here once so app.py and the Celery task can
both call this factory — no drift between FastAPI and worker fallbacks."""
from __future__ import annotations

import os

from langchain_openai import ChatOpenAI


GROQ_URL   = "https://api.groq.com/openai/v1"
NVIDIA_URL = "https://integrate.api.nvidia.com/v1"


def _groq(model: str, key: str) -> ChatOpenAI:
    return ChatOpenAI(
        model       = model,
        temperature = 0.0,
        base_url    = GROQ_URL,
        api_key     = key,
        max_retries = 0,
        timeout     = 120,
    )


def _nim(model: str, key: str) -> ChatOpenAI:
    return ChatOpenAI(
        model       = model,
        temperature = 0.0,
        base_url    = NVIDIA_URL,
        api_key     = key,
        max_retries = 0,
        timeout     = 600,
    )


def build_deprecated_llm_chain() -> ChatOpenAI:
    """Build the 13-model `with_fallbacks` chain (deprecated convention).

    The returned object is a `RunnableWithFallbacks` masquerading as the
    primary `ChatOpenAI`. LangGraph nodes / Neo4jRetriever / DocumentGrader
    all use it as if it were a single model."""
    groq_key = os.environ.get("GROQ_API_KEY", "")
    nvidia_key = os.environ.get("NVIDIA_API_KEY", "")
    models: list[ChatOpenAI] = []
    if groq_key:
        models.extend([
            _groq("llama-3.3-70b-versatile", groq_key),
            _groq("qwen/qwen3-32b",          groq_key),
            _groq("llama-3.1-8b-instant",    groq_key),
        ])
    models.extend([
        _nim("z-ai/glm5",                                 nvidia_key),
        _nim("moonshotai/kimi-k2.5",                      nvidia_key),
        _nim("moonshotai/kimi-k2-instruct",               nvidia_key),
        _nim("deepseek-ai/deepseek-v3.2",                 nvidia_key),
        _nim("nvidia/llama-3.3-nemotron-super-49b-v1.5",  nvidia_key),
        _nim("meta/llama-3.3-70b-instruct",               nvidia_key),
        _nim("meta/llama-3.1-8b-instruct",                nvidia_key),
    ])
    primary = models[0]
    return primary.with_fallbacks(models[1:])
