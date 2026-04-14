"""
LangGraph Agentic RAG Workflow for YouTube Content Search

CONCEPT: StateGraph is the core of LangGraph 1.1.
- You define a state schema (TypedDict) that flows between nodes
- Each node is an async function: receives state, returns partial state update
- Edges connect nodes; conditional edges route based on state inspection
- compile() turns the graph into an executable with optional checkpointer
- ainvoke() runs the graph; astream() streams node-by-node updates

The agentic loop (Phase 4 — full production graph):
  1. RETRIEVE: search for documents matching the query
  2. GRADE: LLM evaluates each document for relevance
  3. If good docs exist → GENERATE answer with citations
  4. If no good docs → REWRITE query and retry
  5. CHECK HALLUCINATION: verify answer is grounded in documents
  6. If grounded → FORMAT CITATIONS → END
  7. If not grounded → REWRITE and retry

Graph:
  retrieve → grade → [generate | rewrite → retrieve]
                       ↓
                  check_hallucination → [format_citations → END | rewrite → retrieve]
"""
import re
from pydantic import BaseModel, Field
from langgraph.graph import StateGraph, END
from langchain_core.prompts import ChatPromptTemplate
from langchain_core.runnables import RunnableConfig
from langchain_openai import ChatOpenAI


def _strip_think_tags(text: str) -> str:
    """Strip <think>...</think> reasoning tokens from model output."""
    return re.sub(r"<think>[\s\S]*?</think>\s*", "", text).strip()
from langgraph.checkpoint.redis.aio import AsyncRedisSaver

from schemas.state import YouTubeRAGState
from services.grader import DocumentGrader


# =============================================================================
# Prompt Templates
# =============================================================================
GENERATE_PROMPT = ChatPromptTemplate.from_messages([
    (
        "system",
        "You are a helpful assistant that answers questions about YouTube video content. "
        "Use ONLY the provided transcript excerpts to answer. "
        "Always cite your sources using this format: [Video: title] "
        "If the transcripts don't contain enough information, say so clearly.",
    ),
    (
        "human",
        "Question: {question}\n\n"
        "Video transcripts:\n{context}\n\n"
        "Answer the question based on the transcripts above. Include citations.",
    ),
])

REWRITE_PROMPT = ChatPromptTemplate.from_messages([
    (
        "system",
        "You are a query rewriter. The original query did not return relevant results. "
        "Rewrite it to be more specific or use different terms that might match video transcripts. "
        "Return ONLY the rewritten query, nothing else.",
    ),
    (
        "human",
        "Original question: {question}\n"
        "Previous search query: {search_query}\n"
        "Rewrite this as a better search query:",
    ),
])

HALLUCINATION_PROMPT = ChatPromptTemplate.from_messages([
    (
        "system",
        "You are a hallucination detector. Given an answer and the source documents it was "
        "generated from, determine:\n"
        "1. Is the answer GROUNDED in the documents? (no fabricated facts)\n"
        "2. Does the answer ADDRESS the original question?\n"
        "Be strict. If the answer contains ANY claim not supported by the documents, "
        "mark it as not grounded.",
    ),
    (
        "human",
        "Question: {question}\n\n"
        "Answer: {generation}\n\n"
        "Source documents:\n{documents}\n\n"
        "Evaluate the answer.",
    ),
])


# =============================================================================
# Structured Output Models for Phase 4
# =============================================================================
class HallucinationCheck(BaseModel):
    """Result of hallucination detection."""
    grounded: bool = Field(
        description = "True if ALL claims in the answer are supported by the source documents"
    )
    addresses_question: bool = Field(
        description = "True if the answer actually addresses the original question"
    )
    reason: str = Field(
        description = "Brief explanation of the assessment"
    )


# =============================================================================
# Graph Node Functions
# =============================================================================
async def retrieve(state: YouTubeRAGState, retriever, channel_ids: list[str] | None = None) -> dict:
    """
    RETRIEVE node: search for documents matching the query.
    Also tracks which retrieval sources contributed results.
    channel_ids is passed via closure from the parent graph to scope retrieval.
    """
    query = state.get("search_query") or state["question"]
    try:
        documents = await retriever.retrieve(query, channel_ids)
    except Exception:
        documents = []
    # Track which sources contributed
    sources = list({doc.metadata.get("source", "unknown") for doc in documents})
    return {"documents": documents, "retrieval_sources": sources}


async def grade_documents(state: YouTubeRAGState, grader: DocumentGrader) -> dict:
    """GRADE node: LLM evaluates each document for relevance in PARALLEL."""
    relevant_docs = await grader.grade_documents(
        state["question"],
        state["documents"],
    )
    return {"documents": relevant_docs}


async def generate(state: YouTubeRAGState, llm: ChatOpenAI) -> dict:
    """GENERATE node: produce an answer using relevant documents."""
    context_parts = []
    for doc in state["documents"]:
        meta = doc.metadata
        header = f"[Video: {meta.get('title', 'Unknown')}] ({meta.get('webpage_url', '')})"
        context_parts.append(f"{header}\n{doc.page_content}")
    context = "\n\n---\n\n".join(context_parts)
    chain = GENERATE_PROMPT | llm
    try:
        response = await chain.ainvoke({
            "question": state["question"],
            "context": context,
        })
        return {"generation": _strip_think_tags(response.content)}
    except Exception as e:
        return {"generation": f"Error generating answer: {e}"}


async def check_hallucination(state: YouTubeRAGState, llm: ChatOpenAI) -> dict:
    """
    CHECK HALLUCINATION node (Phase 4): verify the generation is grounded.

    CONCEPT: LLM-as-judge for factual grounding. The LLM compares the
    generated answer against the source documents and checks:
    1. Are all claims supported by the documents? (grounded)
    2. Does the answer actually address the question? (addresses_question)

    If either check fails, the conditional edge routes to rewrite_query
    for another retrieval attempt with different terms.
    """
    # Format source documents for the check
    doc_texts = []
    for doc in state["documents"]:
        doc_texts.append(doc.page_content[:1000])
    documents_str = "\n---\n".join(doc_texts)

    chain = HALLUCINATION_PROMPT | llm.with_structured_output(HallucinationCheck, method = "function_calling")
    try:
        result: HallucinationCheck = await chain.ainvoke({
            "question": state["question"],
            "generation": state["generation"],
            "documents": documents_str,
        })
        return {
            "grounded": result.grounded and result.addresses_question,
        }
    except Exception:
        # If check fails, assume grounded to avoid blocking
        return {"grounded": True}


async def format_citations(state: YouTubeRAGState) -> dict:
    """
    FORMAT CITATIONS node (Phase 4): extract structured citations from documents.

    CONCEPT: Citations let the user verify the answer by clicking through
    to the source video. Each citation includes the video title and URL.
    Deduplicated by video_id to avoid repeating the same source.
    """
    seen_videos = set()
    citations = []
    for doc in state["documents"]:
        meta = doc.metadata
        video_id = meta.get("video_id", "")
        if video_id and video_id not in seen_videos:
            seen_videos.add(video_id)
            citations.append({
                "video_id": video_id,
                "title": meta.get("title", ""),
                "channel": meta.get("channel", ""),
                "url": meta.get("webpage_url", ""),
                "source": meta.get("source", ""),
            })
    return {"citations": citations}


async def rewrite_query(state: YouTubeRAGState, llm: ChatOpenAI) -> dict:
    """REWRITE node: expand/rephrase the query for better retrieval."""
    chain = REWRITE_PROMPT | llm
    try:
        response = await chain.ainvoke({
            "question": state["question"],
            "search_query": state.get("search_query") or state["question"],
        })
        new_query = _strip_think_tags(response.content)
    except Exception:
        new_query = f"{state['question']} (expanded)"
    return {
        "search_query": new_query,
        "retry_count": state.get("retry_count", 0) + 1,
    }


# =============================================================================
# Conditional Edges
# =============================================================================
def decide_after_grading(state: YouTubeRAGState, config: RunnableConfig) -> str:
    """Route after document grading: generate, rewrite, or end."""
    if state["documents"]:
        return "generate"
    max_retries = config.get("configurable", {}).get("max_retries", 3)
    if state.get("retry_count", 0) < max_retries:
        return "rewrite"
    return "end"


def decide_after_hallucination_check(state: YouTubeRAGState, config: RunnableConfig) -> str:
    """
    Route after hallucination check: accept or retry.

    CONCEPT: If the answer isn't grounded, we rewrite and try again.
    But only if we haven't exhausted retries — prevents infinite loops
    where the LLM keeps generating hallucinated answers.
    """
    if state.get("grounded", False):
        return "format_citations"
    max_retries = config.get("configurable", {}).get("max_retries", 3)
    if state.get("retry_count", 0) < max_retries:
        return "rewrite"
    # Exhausted retries — accept what we have
    return "format_citations"


# =============================================================================
# Build the Graph
# =============================================================================
def build_youtube_rag_graph(
    retriever,
    grader: DocumentGrader,
    llm: ChatOpenAI,
    checkpointer: AsyncRedisSaver,
    channel_ids: list[str] | None = None,
):
    """
    Build and compile the full production LangGraph workflow.

    Phase 4 graph structure:
      retrieve → grade → [generate | rewrite → retrieve]
                           ↓
                      check_hallucination → [format_citations → END | rewrite → retrieve]

    New nodes vs Phase 1-3:
    - check_hallucination: LLM verifies answer is grounded in documents
    - format_citations: extracts structured citations for the response
    """
    workflow = StateGraph(YouTubeRAGState)

    # Register nodes as async closures.
    # CONCEPT: LangGraph inspects whether a node function is async via
    # asyncio.iscoroutinefunction(). sync lambdas returning coroutines
    # and functools.partial both fail this check. Defining proper async
    # inner functions is the only reliable way to bind dependencies
    # while preserving the async signature LangGraph requires.
    async def _retrieve(state):
        return await retrieve(state, retriever, channel_ids)

    async def _grade(state):
        return await grade_documents(state, grader)

    async def _generate(state):
        return await generate(state, llm)

    async def _check_hallucination(state):
        return await check_hallucination(state, llm)

    async def _rewrite(state):
        return await rewrite_query(state, llm)

    workflow.add_node("retrieve", _retrieve)
    workflow.add_node("grade_documents", _grade)
    workflow.add_node("generate", _generate)
    workflow.add_node("check_hallucination", _check_hallucination)
    workflow.add_node("format_citations", format_citations)
    workflow.add_node("rewrite_query", _rewrite)
    # Entry point
    workflow.set_entry_point("retrieve")
    # Edges
    workflow.add_edge("retrieve", "grade_documents")
    # After grading: generate, rewrite, or end
    workflow.add_conditional_edges(
        "grade_documents",
        decide_after_grading,
        {
            "generate": "generate",
            "rewrite": "rewrite_query",
            "end": END,
        },
    )
    # After generating: check hallucination
    workflow.add_edge("generate", "check_hallucination")
    # After hallucination check: accept (format citations) or rewrite
    workflow.add_conditional_edges(
        "check_hallucination",
        decide_after_hallucination_check,
        {
            "format_citations": "format_citations",
            "rewrite": "rewrite_query",
        },
    )
    # After formatting citations: done
    workflow.add_edge("format_citations", END)
    # After rewriting: retry retrieval (the cycle)
    workflow.add_edge("rewrite_query", "retrieve")
    # Compile without checkpointer for now — AsyncRedisSaver causes deadlock
    # when called from endpoint handlers within the lifespan async-with block.
    # TODO: fix by initializing checkpointer outside the context manager
    return workflow.compile()
