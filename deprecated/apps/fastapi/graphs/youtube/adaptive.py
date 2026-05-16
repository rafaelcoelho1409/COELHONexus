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
from langgraph.graph import StateGraph, END
from langgraph.types import Send
from langchain_core.prompts import ChatPromptTemplate
from langchain_openai import ChatOpenAI
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver

from schemas.youtube.state import AdaptiveRAGState
from services.youtube.grader import DocumentGrader
from graphs.youtube.rag import YouTubeContentGraph
from schemas.youtube.agents import (
    QueryClassification,
    CriticAssessment,
    ResearchPlan
)
from schemas.youtube.prompts import (
    CONTEXTUALIZE_PROMPT,
    CLASSIFY_PROMPT,
    DIRECT_ANSWER_PROMPT,
    SYNTHESIZE_PROMPT,
    CRITIC_PROMPT,
)
from graphs.youtube.helpers import (
    _resolve_channel_ids,
    _strip_think_tags
)


class AdaptiveRAGGraph:
    def __init__(self):
        pass
    # =============================================================================
    # Node Functions
    # =============================================================================
    async def contextualize_question(
        self, 
        state: AdaptiveRAGState, 
        llm: ChatOpenAI) -> dict:
        """
        If conversation history exists, rewrite the question to be standalone.
        "Tell me more about that" → "Tell me more about Elon Musk's views on AGI"
        Short-circuits when history is empty (zero cost for first message).
        """
        history = state.get("conversation_history") or []
        if not history:
            return {}  # No history — nothing to contextualize
        # Format history for the prompt
        parts = []
        for turn in history[-5:]:  # Last 5 turns max
            parts.append(f"Q: {turn['question']}\nA: {turn['answer'][:300]}")
        formatted = "\n---\n".join(parts)
        chain = CONTEXTUALIZE_PROMPT | llm
        try:
            response = await chain.ainvoke({
                "history": formatted,
                "question": state["question"],
            })
            rewritten = _strip_think_tags(response.content)
            if rewritten and rewritten != state["question"]:
                return {"question": rewritten, "search_query": rewritten}
        except Exception:
            pass
        return {}

    async def classify_query(
        self, 
        state: AdaptiveRAGState, 
        llm: ChatOpenAI, 
        neo4j_graph = None) -> dict:
        """
        Entry point: classify query complexity → route to FAST/STANDARD/DEEP.
        Also auto-detects channel scope from the question and resolves
        channel names to IDs via Neo4j.
        If force_mode is set, skip the mode classification LLM call.
        """
        # Use API-provided channel_ids if present
        channel_ids = state.get("channel_ids") or []
        force = state.get("force_mode")
        if force and channel_ids:
            return {
                "mode": force, 
                "sub_questions": [], 
                "channel_ids": channel_ids}
        chain = CLASSIFY_PROMPT | llm.with_structured_output(
            QueryClassification, 
            method = "function_calling"
        )
        try:
            result = await chain.ainvoke({"question": state["question"]})
            mode = force or result.mode
            sub_questions = result.sub_questions if mode == "deep" else []
            # Auto-resolve channel names to IDs if not provided by API
            if not channel_ids and result.channel_names and neo4j_graph:
                channel_ids = _resolve_channel_ids(neo4j_graph, result.channel_names)
            return {
                "mode": mode,
                "sub_questions": sub_questions,
                "channel_ids": channel_ids,
            }
        except Exception:
            return {
                "mode": force or "standard", 
                "sub_questions": [], 
                "channel_ids": channel_ids}

    async def direct_answer(
        self,
        state: AdaptiveRAGState, 
        llm: ChatOpenAI) -> dict:
        """FAST path: direct LLM answer without retrieval."""
        chain = DIRECT_ANSWER_PROMPT | llm
        try:
            response = await chain.ainvoke({"question": state["question"]})
            return {
                "generation": _strip_think_tags(response.content),
                "grounded": True,
                "citations": [],
                "retrieval_sources": [],
            }
        except Exception as e:
            return {"generation": f"Error: {e}", "grounded": False}

    async def run_standard_pipeline(
        self,
        state: AdaptiveRAGState, 
        standard_graph) -> dict:
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
            result = await standard_graph.ainvoke(initial, config = config)
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

    async def plan_research(
        self, 
        state: AdaptiveRAGState, 
        llm: ChatOpenAI) -> dict:
        """
        DEEP path entry: if classifier already generated sub_questions, use them.
        Otherwise, generate a research plan with the planner LLM.
        """
        if state.get("sub_questions"):
            return {
                "research_plan": f"Investigating {len(state['sub_questions'])} aspects of: {state['question']}",
            }
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
        chain = prompt | llm.with_structured_output(
            ResearchPlan, 
            method = "function_calling")
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

    async def run_subagent(
        self,
        payload: dict, 
        standard_graph) -> dict:
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
            result = await standard_graph.ainvoke(initial, config = config)
        except Exception as e:
            result = {
                "generation": f"Subagent error: {e}", 
                "citations": [], 
                "grounded": False, 
                "retrieval_sources": []}
        return {
            "sub_results": [{
                "sub_question": sub_q,
                "answer": result.get("generation", ""),
                "citations": result.get("citations", []),
                "grounded": result.get("grounded", False),
                "retrieval_sources": result.get("retrieval_sources", []),
            }],
        }

    async def synthesize(
        self,
        state: AdaptiveRAGState, 
        llm: ChatOpenAI) -> dict:
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
            generation = _strip_think_tags(response.content)
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

    async def critic(
        self,
        state: AdaptiveRAGState, 
        llm: ChatOpenAI) -> dict:
        """
        Validate the synthesis against sub-research evidence.
        Returns confidence score and grounding assessment.
        """
        parts = []
        for sr in state.get("sub_results", []):
            parts.append(f"Q: {sr['sub_question']}\nA: {sr['answer']}")
        sub_results_text = "\n---\n".join(parts)
        chain = CRITIC_PROMPT | llm.with_structured_output(
            CriticAssessment, 
            method = "function_calling"
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
    def route_by_mode(
        self, 
        state: AdaptiveRAGState) -> str | list:
        """Route after classification: FAST, STANDARD, or DEEP."""
        mode = state.get("mode", "standard").lower()
        if mode == "fast":
            return "direct_answer"
        elif mode == "deep":
            return "plan_research"
        return "run_standard"


    def fan_out_subagents(
        self,
        state: AdaptiveRAGState) -> list:
        """
        After planning, fan out sub-questions to parallel subagents via Send().
        Each Send() targets the run_subagent node with one sub-question.
        """
        channel_ids = state.get("channel_ids") or []
        return [
            Send("run_subagent", {"sub_question": q, "channel_ids": channel_ids})
            for q in state.get("sub_questions", [])
        ]


    # =============================================================================
    # Build the Adaptive Graph
    # =============================================================================
    def build_adaptive_rag_graph(
        self,
        retriever,
        grader: DocumentGrader,
        llm: ChatOpenAI,
        checkpointer: AsyncPostgresSaver,
        neo4j_graph = None,
    ):
        """
        Build the Adaptive RAG parent graph.

        Wraps the existing STANDARD pipeline as a subgraph and adds
        FAST (direct answer) and DEEP (multi-agent research) paths.

        Channel scope: the classifier auto-detects channel names from the
        question, resolves them to IDs via Neo4j, and passes channel_ids
        to the STANDARD subgraph. This ensures single-channel queries
        don't get contaminated by other channels' content.
        """
        # Instantiate YouTubeContentGraph once; reuse its method to build
        # channel-scoped compiled graphs per request.
        youtube_graph_builder = YouTubeContentGraph()
        def _build_standard_graph(channel_ids: list[str] | None = None):
            """Build a STANDARD pipeline scoped to specific channels."""
            return youtube_graph_builder.build_youtube_rag_graph(
                retriever = retriever,
                grader = grader,
                llm = llm,
                checkpointer = checkpointer,
                channel_ids = channel_ids or None,
            )
        # Build parent graph
        workflow = StateGraph(AdaptiveRAGState)
        # Bind dependencies via async closures.
        # These are local functions (NOT methods), so they take only the node
        # parameter and close over `self`, `llm`, `neo4j_graph`, etc. from the
        # enclosing scope. Registering them directly with add_node keeps the
        # class structure while letting LangGraph detect them as async.
        async def _contextualize(state):
            return await self.contextualize_question(state, llm)
        async def _classify(state):
            return await self.classify_query(state, llm, neo4j_graph)
        async def _direct_answer(state):
            return await self.direct_answer(state, llm)
        async def _run_standard(state):
            # Build a channel-scoped STANDARD graph for this request
            scoped_graph = _build_standard_graph(state.get("channel_ids"))
            return await self.run_standard_pipeline(state, scoped_graph)
        async def _plan_research(state):
            return await self.plan_research(state, llm)
        async def _run_subagent(payload):
            # Subagents inherit the channel scope from the parent state
            channel_ids = payload.get("channel_ids")
            scoped_graph = _build_standard_graph(channel_ids)
            return await self.run_subagent(payload, scoped_graph)
        async def _synthesize(state):
            return await self.synthesize(state, llm)
        async def _critic(state):
            return await self.critic(state, llm)
        # Register nodes (local closures, not method references)
        workflow.add_node("contextualize", _contextualize)
        workflow.add_node("classify_query", _classify)
        workflow.add_node("direct_answer", _direct_answer)
        workflow.add_node("run_standard", _run_standard)
        workflow.add_node("plan_research", _plan_research)
        workflow.add_node("run_subagent", _run_subagent)
        workflow.add_node("synthesize", _synthesize)
        workflow.add_node("critic", _critic)
        # Entry point: contextualize → classify → route
        workflow.set_entry_point("contextualize")
        workflow.add_edge("contextualize", "classify_query")
        # After classification: route to FAST, STANDARD, or DEEP
        workflow.add_conditional_edges(
            "classify_query",
            self.route_by_mode,
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
        workflow.add_conditional_edges("plan_research", self.fan_out_subagents, ["run_subagent"])
        workflow.add_edge("run_subagent", "synthesize")
        workflow.add_edge("synthesize", "critic")
        workflow.add_edge("critic", END)
        return workflow.compile()
