"""
Knowledge Distiller — Scope Gate Service

CONCEPT: Pre-flight classifier that runs BEFORE any expensive work
(Celery task, LLM synthesis tokens, Crawl4AI fetches). Answers one
question: "Is this input a code framework worth distilling?"

PATTERN: The same `PROMPT | llm.with_structured_output(Model)` idiom
used throughout the project (see graphs/youtube/adaptive.py::classify_query
and services/youtube/grader.py for reference implementations). The Pydantic
schema `ScopeValidation` enforces the output shape; the LLM is a classifier,
not a free-form generator.

MODEL CHOICE (recommended): a dedicated fast classifier model — Groq
`llama-3.1-8b-instant` on the free tier (60 RPM, 840 TPS, typical
latency <500ms). The router wires the instance in at Step 11. Passing
the app's 19-model fallback chain also works but burns primary-model
quota on trivial binary classification.

FAILURE MODE — fail CLOSED:
An unverified scope is a DoS vector: a user could request "distill War
and Peace" and each such request would enqueue ~30 min of Celery work
before the pipeline itself noticed the mismatch. So if the classifier
itself fails (network, 429, timeout), we RAISE — the router catches and
returns HTTP 503. Silent-allow would fail OPEN; we fail CLOSED.
"""
from langchain_openai import ChatOpenAI

from schemas.knowledge.agents import ScopeValidation
from schemas.knowledge.prompts import SCOPE_PROMPT


async def classify_scope(
    framework: str,
    llm: ChatOpenAI) -> ScopeValidation:
    """
    Run the scope classifier on the user's `framework` input.

    Args:
        framework: the raw `CreateStudyRequest.framework` string.
                   Already non-empty (Pydantic validated at the router).
        llm: any LangChain chat model supporting function calling —
             ChatOpenAI, a RunnableWithFallbacks, etc. Recommended: a
             dedicated cheap/fast model such as Groq
             `llama-3.1-8b-instant` bound at `app.state.llm_scope`.

    Returns:
        ScopeValidation — caller inspects `is_code_framework`:
          - True  → enqueue the Celery distiller task
          - False → return HTTP 400 with `rejection_reason`

    Raises:
        RuntimeError: the LLM classification call failed. The FastAPI
        router at Step 11 catches this and returns HTTP 503. We fail
        CLOSED: unverifiable scope must not reach expensive work.
    """
    chain = SCOPE_PROMPT | llm.with_structured_output(
        ScopeValidation,
        method = "function_calling",
    )
    try:
        result = await chain.ainvoke({"framework": framework})
    except Exception as e:
        raise RuntimeError(f"Scope classifier failed: {e}") from e
    return result
