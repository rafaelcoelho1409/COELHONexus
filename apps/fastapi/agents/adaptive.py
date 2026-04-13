"""
Adaptive RAG — Dual-Mode Parent Graph (FAST / STANDARD / DEEP)

CONCEPT: A query classifier routes each question to the best strategy:
- FAST: simple factual → direct LLM answer, skip retrieval (<2s)
- STANDARD: evidence-based → full RAG pipeline with citations (15-60s)
- DEEP: analytical → planner decomposes into sub-questions, parallel
  subagents each run the STANDARD pipeline, synthesizer merges findings,
  critic validates (30-120s)

The existing build_youtube_rag_graph() is used as-is for STANDARD and
as the engine for each DEEP subagent. No changes to the core pipeline.

Architecture:
                    START
                      |
              classify_query
               /      |      \
           FAST    STANDARD    DEEP
            |         |          |
        direct     [existing   plan_research
        answer     pipeline]       |
            |         |       fan_out_subagents (parallel)
            |         |            |
            |         |        synthesize
            |         |            |
            |         |          critic
            v         v            v
                     END
"""
import asyncio
from pydantic import BaseModel, Field
from langgraph.graph import StateGraph, END
from langgraph.types import Send
from langchain_core.prompts import ChatPromptTemplate
from langchain_openai import ChatOpenAI
from langgraph.checkpoint.redis.aio import AsyncRedisSaver

from schemas.state import AdaptiveRAGState
from services.grader import DocumentGrader
from agents.youtube import build_youtube_rag_graph


# =============================================================================
# Structured Output Models
# =============================================================================
class QueryClassification(BaseModel):
    """Output of the query classifier."""
    mode: str = Field(
        description="Query mode: 'fast' for simple factual, 'standard' for evidence-based, 'deep' for analytical"
    )
    reasoning: str = Field(
        description="Brief explanation of why this mode was chosen"
    )
    sub_questions: list[str] = Field(
        default_factory=list,
        description="For 'deep' mode: 3-8 focused sub-questions to investigate"
    )


class CriticAssessment(BaseModel):
    """Output of the critic node."""
    confidence_score: float = Field(
        description="Confidence in the synthesis quality (0.0-1.0)"
    )
    claims_supported: bool = Field(
        description="True if all claims in the synthesis are supported by subagent evidence"
    )
    reasoning: str = Field(
        description="Brief explanation of the assessment"
    )


# =============================================================================
# Prompt Templates
# =============================================================================
CLASSIFY_PROMPT = ChatPromptTemplate.from_messages([
    (
        "system",
        "You are a query complexity classifier for a YouTube transcript search system. "
        "Classify the user's question into one of three modes:\n\n"
        "FAST — Simple factual questions answerable from general knowledge. "
        "Examples: 'What is citizenship by investment?', 'What does CBI stand for?'\n\n"
        "STANDARD — Questions that need evidence from video transcripts. "
        "Examples: 'What does Wealthy Expat say about Dubai?', "
        "'Compare Dominica vs Grenada for citizenship', "
        "'What are the tax benefits of living in Dubai?'\n\n"
        "DEEP — Analytical questions requiring multi-faceted analysis across many videos. "
        "Pattern-finding, psychological analysis, contradiction detection, hidden assumptions. "
        "Examples: 'What psychological traits does this creator show?', "
        "'What contradictions exist across all videos?', "
        "'What hidden assumptions does this channel never question?'\n\n"
        "When uncertain, default to STANDARD.\n"
        "For DEEP mode, also generate 3-8 focused sub-questions that break down the analysis.",
    ),
    ("human", "{question}"),
])

DIRECT_ANSWER_PROMPT = ChatPromptTemplate.from_messages([
    (
        "system",
        "You are a helpful assistant. Answer the user's question concisely from your "
        "general knowledge. If you are uncertain or the question requires specific "
        "video transcript evidence, say so clearly.",
    ),
    ("human", "{question}"),
])

SYNTHESIZE_PROMPT = ChatPromptTemplate.from_messages([
    (
        "system",
        "You are a research synthesizer. You receive the results of multiple parallel "
        "research sub-questions about the same overarching topic. Your job is to:\n"
        "1. Combine all findings into a coherent analytical report\n"
        "2. Cross-reference findings — identify patterns that emerge across sub-questions\n"
        "3. Note any contradictions or tensions between findings\n"
        "4. Structure the output clearly with sections\n"
        "5. Cite sources using [Video: title] format\n"
        "Do NOT fabricate information. Only synthesize what the sub-research found.",
    ),
    (
        "human",
        "Original question: {question}\n\n"
        "Research plan: {research_plan}\n\n"
        "Sub-research findings:\n{sub_results}\n\n"
        "Synthesize these findings into a comprehensive analytical report.",
    ),
])

CRITIC_PROMPT = ChatPromptTemplate.from_messages([
    (
        "system",
        "You are a research quality critic. Evaluate the synthesis against the "
        "sub-research findings. Check:\n"
        "1. Is every claim in the synthesis supported by at least one sub-research finding?\n"
        "2. Are there contradictions within the synthesis itself?\n"
        "3. Did the synthesis adequately cover all sub-questions?\n"
        "4. Assign a confidence score from 0.0 (unreliable) to 1.0 (fully supported).\n"
        "Be strict but fair.",
    ),
    (
        "human",
        "Original question: {question}\n\n"
        "Synthesis:\n{synthesis}\n\n"
        "Sub-research findings:\n{sub_results}\n\n"
        "Evaluate the synthesis.",
    ),
])


# =============================================================================
# Node Functions
# =============================================================================
async def classify_query(state: AdaptiveRAGState, llm: ChatOpenAI) -> dict:
    """
    Entry point: classify query complexity → route to FAST/STANDARD/DEEP.
    If force_mode is set, skip the LLM call.
    """
    force = state.get("force_mode")
    if force:
        return {"mode": force, "sub_questions": []}

    chain = CLASSIFY_PROMPT | llm.with_structured_output(
        QueryClassification, method="function_calling"
    )
    try:
        result = await chain.ainvoke({"question": state["question"]})
        return {
            "mode": result.mode,
            "sub_questions": result.sub_questions if result.mode == "deep" else [],
        }
    except Exception:
        return {"mode": "standard", "sub_questions": []}


async def direct_answer(state: AdaptiveRAGState, llm: ChatOpenAI) -> dict:
    """FAST path: direct LLM answer without retrieval."""
    chain = DIRECT_ANSWER_PROMPT | llm
    try:
        response = await chain.ainvoke({"question": state["question"]})
        return {
            "generation": response.content,
            "grounded": True,
            "citations": [],
            "retrieval_sources": [],
        }
    except Exception as e:
        return {"generation": f"Error: {e}", "grounded": False}


async def run_standard_pipeline(state: AdaptiveRAGState, standard_graph) -> dict:
    """
    STANDARD path: invoke the existing RAG pipeline as a subgraph.
    Maps YouTubeRAGState output back to AdaptiveRAGState fields.
    """
    initial = {
        "question": state["question"],
        "documents": [],
        "generation": "",
        "retry_count": 0,
        "search_query": state.get("search_query") or state["question"],
        "grounded": False,
        "citations": [],
        "retrieval_sources": [],
    }
    config = {"recursion_limit": 30}
    try:
        result = await standard_graph.ainvoke(initial, config=config)
    except Exception as e:
        return {"generation": f"Pipeline error: {e}", "grounded": False}

    return {
        "generation": result.get("generation", ""),
        "citations": result.get("citations", []),
        "grounded": result.get("grounded", False),
        "retrieval_sources": result.get("retrieval_sources", []),
        "retry_count": result.get("retry_count", 0),
        "search_query": result.get("search_query", state["question"]),
    }


async def plan_research(state: AdaptiveRAGState, llm: ChatOpenAI) -> dict:
    """
    DEEP path entry: if classifier already generated sub_questions, use them.
    Otherwise, generate a research plan with the planner LLM.
    """
    if state.get("sub_questions"):
        return {
            "research_plan": f"Investigating {len(state['sub_questions'])} aspects of: {state['question']}",
        }

    from pydantic import BaseModel as BM, Field as F
    class ResearchPlan(BM):
        sub_questions: list[str] = F(description="3-8 focused sub-questions")
        strategy: str = F(description="Brief research strategy")

    prompt = ChatPromptTemplate.from_messages([
        (
            "system",
            "You are a research planner. Decompose the user's analytical question "
            "into 3-8 focused sub-questions that, when answered individually from "
            "video transcripts, will provide the evidence needed for a comprehensive "
            "analysis. Each sub-question should target a specific angle or pattern.",
        ),
        ("human", "{question}"),
    ])
    chain = prompt | llm.with_structured_output(ResearchPlan, method="function_calling")
    try:
        result = await chain.ainvoke({"question": state["question"]})
        return {
            "sub_questions": result.sub_questions,
            "research_plan": result.strategy,
        }
    except Exception:
        # Fallback: split into generic sub-questions
        return {
            "sub_questions": [
                f"What patterns emerge regarding: {state['question']}",
                f"What contradictions exist regarding: {state['question']}",
                f"What is frequently repeated about: {state['question']}",
            ],
            "research_plan": "Fallback: generic pattern analysis",
        }


async def run_subagent(payload: dict, standard_graph) -> dict:
    """
    DEEP subagent: runs the STANDARD pipeline for one sub-question.

    Called via LangGraph Send() — receives a minimal payload dict,
    not the full AdaptiveRAGState. Returns into sub_results via
    operator.add reducer.
    """
    sub_q = payload["sub_question"]
    initial = {
        "question": sub_q,
        "documents": [],
        "generation": "",
        "retry_count": 0,
        "search_query": sub_q,
        "grounded": False,
        "citations": [],
        "retrieval_sources": [],
    }
    config = {"recursion_limit": 30}
    try:
        result = await standard_graph.ainvoke(initial, config=config)
    except Exception as e:
        result = {"generation": f"Subagent error: {e}", "citations": [], "grounded": False, "retrieval_sources": []}

    return {
        "sub_results": [{
            "sub_question": sub_q,
            "answer": result.get("generation", ""),
            "citations": result.get("citations", []),
            "grounded": result.get("grounded", False),
            "retrieval_sources": result.get("retrieval_sources", []),
        }],
    }


async def synthesize(state: AdaptiveRAGState, llm: ChatOpenAI) -> dict:
    """
    Combine all subagent results into a unified analytical report.
    Merge citations from all sub-results, deduplicate by video_id.
    """
    # Format sub-results for the prompt
    parts = []
    for i, sr in enumerate(state.get("sub_results", []), 1):
        parts.append(
            f"### Sub-question {i}: {sr['sub_question']}\n"
            f"**Answer:** {sr['answer']}\n"
            f"**Grounded:** {sr['grounded']}\n"
            f"**Sources:** {', '.join(sr.get('retrieval_sources', []))}"
        )
    sub_results_text = "\n\n".join(parts)

    chain = SYNTHESIZE_PROMPT | llm
    try:
        response = await chain.ainvoke({
            "question": state["question"],
            "research_plan": state.get("research_plan", ""),
            "sub_results": sub_results_text,
        })
        generation = response.content
    except Exception as e:
        generation = f"Synthesis error: {e}"

    # Merge and deduplicate citations from all sub-results
    seen_videos = set()
    merged_citations = []
    all_sources = set()
    for sr in state.get("sub_results", []):
        for cit in sr.get("citations", []):
            vid = cit.get("video_id", "")
            if vid and vid not in seen_videos:
                seen_videos.add(vid)
                merged_citations.append(cit)
        for src in sr.get("retrieval_sources", []):
            all_sources.add(src)

    return {
        "generation": generation,
        "citations": merged_citations,
        "retrieval_sources": list(all_sources),
    }


async def critic(state: AdaptiveRAGState, llm: ChatOpenAI) -> dict:
    """
    Validate the synthesis against sub-research evidence.
    Returns confidence score and grounding assessment.
    """
    parts = []
    for sr in state.get("sub_results", []):
        parts.append(f"Q: {sr['sub_question']}\nA: {sr['answer']}")
    sub_results_text = "\n---\n".join(parts)

    chain = CRITIC_PROMPT | llm.with_structured_output(
        CriticAssessment, method="function_calling"
    )
    try:
        result = await chain.ainvoke({
            "question": state["question"],
            "synthesis": state.get("generation", ""),
            "sub_results": sub_results_text,
        })
        return {
            "confidence_score": result.confidence_score,
            "grounded": result.claims_supported,
        }
    except Exception:
        return {"confidence_score": 0.5, "grounded": True}


# =============================================================================
# Conditional Edges
# =============================================================================
def route_by_mode(state: AdaptiveRAGState) -> str | list:
    """Route after classification: FAST, STANDARD, or DEEP."""
    mode = state.get("mode", "standard")
    if mode == "fast":
        return "direct_answer"
    elif mode == "deep":
        return "plan_research"
    return "run_standard"


def fan_out_subagents(state: AdaptiveRAGState) -> list:
    """
    After planning, fan out sub-questions to parallel subagents via Send().
    Each Send() targets the run_subagent node with one sub-question.
    """
    return [
        Send("run_subagent", {"sub_question": q})
        for q in state.get("sub_questions", [])
    ]


# =============================================================================
# Build the Adaptive Graph
# =============================================================================
def build_adaptive_rag_graph(
    retriever,
    grader: DocumentGrader,
    llm: ChatOpenAI,
    checkpointer: AsyncRedisSaver,
):
    """
    Build the Adaptive RAG parent graph.

    Wraps the existing STANDARD pipeline as a subgraph and adds
    FAST (direct answer) and DEEP (multi-agent research) paths.
    """
    # Compile the existing STANDARD pipeline as a reusable subgraph
    standard_graph = build_youtube_rag_graph(
        retriever=retriever,
        grader=grader,
        llm=llm,
        checkpointer=checkpointer,
    )

    # Build parent graph
    workflow = StateGraph(AdaptiveRAGState)

    # Bind dependencies via async closures
    async def _classify(state):
        return await classify_query(state, llm)

    async def _direct_answer(state):
        return await direct_answer(state, llm)

    async def _run_standard(state):
        return await run_standard_pipeline(state, standard_graph)

    async def _plan_research(state):
        return await plan_research(state, llm)

    async def _run_subagent(payload):
        return await run_subagent(payload, standard_graph)

    async def _synthesize(state):
        return await synthesize(state, llm)

    async def _critic(state):
        return await critic(state, llm)

    # Register nodes
    workflow.add_node("classify_query", _classify)
    workflow.add_node("direct_answer", _direct_answer)
    workflow.add_node("run_standard", _run_standard)
    workflow.add_node("plan_research", _plan_research)
    workflow.add_node("run_subagent", _run_subagent)
    workflow.add_node("synthesize", _synthesize)
    workflow.add_node("critic", _critic)

    # Entry point
    workflow.set_entry_point("classify_query")

    # After classification: route to FAST, STANDARD, or DEEP
    workflow.add_conditional_edges(
        "classify_query",
        route_by_mode,
        {
            "direct_answer": "direct_answer",
            "run_standard": "run_standard",
            "plan_research": "plan_research",
        },
    )

    # FAST path → END
    workflow.add_edge("direct_answer", END)

    # STANDARD path → END
    workflow.add_edge("run_standard", END)

    # DEEP path: plan → fan out subagents → synthesize → critic → END
    workflow.add_conditional_edges("plan_research", fan_out_subagents, ["run_subagent"])
    workflow.add_edge("run_subagent", "synthesize")
    workflow.add_edge("synthesize", "critic")
    workflow.add_edge("critic", END)

    return workflow.compile()
