"""ycs/agents — LLM chain factory for the Adaptive RAG graph.

2026-06-10 REWIRE: delegates to the LLM rotator's
`build_llm_fallback_chain()` instead of the hand-coded
`with_fallbacks` chain it used to be (now in git history as the
`_legacy_with_fallbacks_chain` shape).

The previous chain hardcoded `z-ai/glm5` as the primary NIM arm —
that model reached end-of-life on 2026-05-18 and started returning
HTTP 410 on every call (`The model 'z-ai/glm5' has reached its end
of life`). The grader, Neo4j retriever's entity extraction, and
every Adaptive RAG node silently failed; observed effect was empty
answers and an Ask page that looked broken.

Why delegate to the rotator chain instead of patching the model
list:

  - The rotator's `ChatLiteLLMRouter` already carries every fix
    shipped this session: FGTS-VA bandit pick, SDK retry kill,
    slot-leak release, `TimeoutErrorRetries=0`, dynamic catalog
    refresh on BYOK changes, free-tier-only constraint.
  - Same code path DD synth + the YCS Neo4j ingestion task use, so
    Ask inherits every future rotator improvement without per-
    consumer drift.
  - Cross-provider compatibility (Groq + NIM + Gemini + Mistral
    etc.) is the rotator's job — we no longer maintain a parallel
    fallback list here.

`with_structured_output(Model, method="function_calling")` keeps
working — the rotator chain forwards it to LiteLLM's Router which
handles the function-calling translation per provider. The grader
and classifier/critic/plan/hallucination nodes all use that pattern
and don't need any code change.

Used by:
  - SmartRetriever's Neo4jRetriever (entity extraction in queries)
  - DocumentGrader (relevance grading)
  - The Adaptive RAG graph nodes (classify, contextualize, generate,
    hallucination, rewrite, plan, synthesize, critic, direct_answer)"""
from __future__ import annotations

from domains.llm.rotator.chain import build_llm_fallback_chain


def build_deprecated_llm_chain():
    """Backward-compat name kept so `app.py`'s lifespan and any other
    importers don't have to change. Returns the rotator's general-
    purpose fallback chain over the unified `dd-all` pool.

    See module docstring for the why."""
    return build_llm_fallback_chain()
