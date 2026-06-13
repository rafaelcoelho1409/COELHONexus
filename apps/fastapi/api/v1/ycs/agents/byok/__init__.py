"""ycs/agents/byok — user-supplied LLM override for the Adaptive RAG graph.

The `LLMConfig` Pydantic model (`schemas.py:LLMConfig`) is persisted to
Redis JSON `coelhonexus:youtube:agents:config` by the PUT endpoint. This
package reads it back per request and builds a single-model LangChain
LLM that the graph nodes / grader / Neo4j retriever consume INSTEAD of
the rotator's `with_fallbacks` chain.

When NO config (or no `api_key`) is present, the request falls back to
`app.state.llm` — the rotator chain — and behavior is unchanged.

Split per `docs/CODE-CONVENTIONS.md`:
  - `domain.py`  — pure: provider-prefix normalization, ChatLiteLLM build
  - `service.py` — I/O: Redis read, async ping for the Test button
  - `keys.py`    — Redis key constant"""
from __future__ import annotations

from .domain  import build_byok_llm, normalize_provider
from .keys    import CONFIG_REDIS_KEY
from .service import get_byok_config, ping_byok


__all__ = [
    "CONFIG_REDIS_KEY",
    "build_byok_llm",
    "get_byok_config",
    "normalize_provider",
    "ping_byok",
]
