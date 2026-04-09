"""
LangGraph Agentic RAG Workflow for YouTube Content Search

CONCEPT: StateGraph is the core of LangGraph 1.1.
- You define a state schema (TypedDict) that flows between nodes
- Each node is an async function: receives state, returns partial state update
- Edges connect nodes; conditional edges route based on state inspection
- compile() turns the graph into an executable with optional checkpointer
- ainvoke() runs the graph; astream() streams node-by-node updates

The agentic loop:
  1. RETRIEVE: search for documents matching the query
  2. GRADE: LLM evaluates each document for relevance
  3. If good docs exist → GENERATE answer with citations
  4. If no good docs → REWRITE query and retry (up to max_retries)

IMPORTANT LangGraph 1.1 notes:
- recursion_limit goes in ainvoke(config={"recursion_limit": N}), NOT in compile()
- compile(checkpointer=...) enables conversation persistence
- astream(version="v2") gives type-safe streaming chunks
"""
from langgraph.graph import StateGraph, END
from langchain_core.prompts import ChatPromptTemplate
from langchain_openai import ChatOpenAI
from langgraph.checkpoint.redis.aio import AsyncRedisSaver

from schemas.state import YouTubeRAGState
from services.retriever import ElasticsearchRetriever
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


# =============================================================================
# Graph Node Functions
# =============================================================================
async def retrieve(state: YouTubeRAGState, retriever: ElasticsearchRetriever) -> dict:
    """
    RETRIEVE node: search for documents matching the query.
    Uses search_query (which may have been rewritten) instead of original question.
    """
    query = state.get("search_query") or state["question"]
    documents = await retriever.retrieve(query)
    return {"documents": documents}


async def grade_documents(state: YouTubeRAGState, grader: DocumentGrader) -> dict:
    """
    GRADE node: LLM evaluates each document for relevance.
    Filters out irrelevant documents. The conditional edge after this node
    checks if any relevant documents remain.
    """
    relevant_docs = await grader.grade_documents(
        state["question"],
        state["documents"],
    )
    return {"documents": relevant_docs}


async def generate(state: YouTubeRAGState, llm: ChatOpenAI) -> dict:
    """
    GENERATE node: produce an answer using relevant documents.
    Formats documents as context and asks the LLM to answer with citations.
    """
    # Format documents as context
    context_parts = []
    for doc in state["documents"]:
        meta = doc.metadata
        header = f"[Video: {meta.get('title', 'Unknown')}] ({meta.get('webpage_url', '')})"
        context_parts.append(f"{header}\n{doc.page_content}")
    context = "\n\n---\n\n".join(context_parts)

    # Generate answer
    chain = GENERATE_PROMPT | llm
    response = await chain.ainvoke({
        "question": state["question"],
        "context": context,
    })
    return {"generation": response.content}


async def rewrite_query(state: YouTubeRAGState, llm: ChatOpenAI) -> dict:
    """
    REWRITE node: expand/rephrase the query for better retrieval.
    Increments retry_count. The retrieve node will use the new search_query.
    """
    chain = REWRITE_PROMPT | llm
    response = await chain.ainvoke({
        "question": state["question"],
        "search_query": state.get("search_query") or state["question"],
    })
    return {
        "search_query": response.content.strip(),
        "retry_count": state.get("retry_count", 0) + 1,
    }


# =============================================================================
# Conditional Edge: decide what to do after grading
# =============================================================================
def decide_after_grading(state: YouTubeRAGState) -> str:
    """
    CONCEPT: Conditional edges inspect the state and return a string key
    that maps to the next node. This is how LangGraph implements branching.
    """
    if state["documents"]:
        return "generate"
    # No relevant docs — check retry budget
    max_retries = state.get("_max_retries", 3)
    if state.get("retry_count", 0) < max_retries:
        return "rewrite"
    return "end"


# =============================================================================
# Build the Graph
# =============================================================================
def build_youtube_rag_graph(
    retriever: ElasticsearchRetriever,
    grader: DocumentGrader,
    llm: ChatOpenAI,
    checkpointer: AsyncRedisSaver,
):
    """
    Build and compile the LangGraph agentic RAG workflow.

    CONCEPT: StateGraph lifecycle:
    1. StateGraph(schema) — define the state type
    2. add_node("name", func) — register processing functions
    3. set_entry_point("name") — where execution starts
    4. add_edge("from", "to") — unconditional transitions
    5. add_conditional_edges("from", func, mapping) — branching
    6. compile(checkpointer=...) — produce executable graph

    The checkpointer enables:
    - Conversation persistence (same thread_id resumes from last state)
    - Crash recovery (interrupted runs can be resumed)
    - State inspection for debugging
    """
    workflow = StateGraph(YouTubeRAGState)

    # Add nodes — each wraps the service dependencies via closures
    workflow.add_node("retrieve", lambda state: retrieve(state, retriever))
    workflow.add_node("grade_documents", lambda state: grade_documents(state, grader))
    workflow.add_node("generate", lambda state: generate(state, llm))
    workflow.add_node("rewrite_query", lambda state: rewrite_query(state, llm))

    # Set entry point
    workflow.set_entry_point("retrieve")

    # Edges
    workflow.add_edge("retrieve", "grade_documents")

    # Conditional edge after grading: generate, rewrite, or end
    workflow.add_conditional_edges(
        "grade_documents",
        decide_after_grading,
        {
            "generate": "generate",
            "rewrite": "rewrite_query",
            "end": END,
        },
    )

    # After rewriting, retry retrieval (this creates the cycle)
    workflow.add_edge("rewrite_query", "retrieve")

    # After generating, we're done
    workflow.add_edge("generate", END)

    # Compile with checkpointer for persistence
    return workflow.compile(checkpointer=checkpointer)
