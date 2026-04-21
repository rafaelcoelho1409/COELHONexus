"""
Knowledge Distiller — Crossover Decomposer

Pre-pass LLM call that splits a combined-study request into canonical topics.
Examples:
  "FastAPI"                            → 1 topic (not crossover)
  "DeepAgents + LangChain + LangGraph" → 3 topics (crossover)
  "Grafana Alloy + LGTM + PromQL"      → 5 topics (LGTM expands to 4; PromQL → Prometheus)

Canonicalization collapses query-language aliases into their parent product
(LogQL → Loki, PromQL → Prometheus, River → Grafana Alloy, PySpark →
Apache Spark). This dedupes the resolver fan-out so we don't spend two LLM
calls resolving the same underlying docs.

Pattern — same `PROMPT | llm.with_structured_output(Model)` idiom the scope
gate uses (services/knowledge/scope.py). Cheap classifier: ~500ms on Groq
8B-class. Fails CLOSED: if the LLM call raises, we fall back to a
deterministic single-topic decomposition instead of exploding the pipeline.
"""
import logging

from langchain_openai import ChatOpenAI

from schemas.knowledge.prompts import RESOLVER_DECOMPOSE_PROMPT
from schemas.knowledge.resolver import DecompositionResult, DecompositionTopic


logger = logging.getLogger(__name__)


async def decompose(
    framework: str,
    aliases: list[str] | None,
    llm: ChatOpenAI) -> DecompositionResult:
    """
    Decompose a resolver request into canonical topics.

    Args:
        framework: raw input string (single framework or crossover).
        aliases: optional synonym list (e.g. ['LGTM', 'monitoring stack']).
        llm: any LangChain chat model supporting function_calling. Recommended:
             Groq `llama-3.1-8b-instant` for ~500ms latency.

    Returns:
        DecompositionResult — single-topic result for plain framework names,
        multi-topic for crossover requests.
    """
    aliases = aliases or []
    chain = RESOLVER_DECOMPOSE_PROMPT | llm.with_structured_output(
        DecompositionResult,
        method = "function_calling",
    )
    try:
        result = await chain.ainvoke({
            "framework": framework,
            "aliases": aliases or "(none)",
        })
    except Exception as e:
        logger.warning(
            f"[crossover] decomposer LLM failed for {framework!r}: {e} — "
            f"falling back to single-topic"
        )
        # Deterministic fallback — caller can still run Stages A-D on the
        # raw framework string. Registry + SearXNG will make the call.
        return DecompositionResult(
            is_crossover = False,
            topics = [
                DecompositionTopic(
                    topic = framework,
                    canonical_name = framework.strip(),
                    reason = "fallback (LLM decomposer unavailable)",
                ),
            ],
        )
    logger.info(
        f"[crossover] {framework!r} → {len(result.topics)} topics "
        f"(crossover={result.is_crossover}): "
        f"{[t.canonical_name for t in result.topics]}"
    )
    return result
