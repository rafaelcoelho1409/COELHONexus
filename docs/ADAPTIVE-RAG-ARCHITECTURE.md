# Adaptive RAG Architecture — Dual-Mode with Deep Research

> Extends the current Agentic RAG with adaptive query routing and multi-agent deep analysis.
> Based on: Agentic RAG Survey (arXiv:2501.09136), Anthropic Multi-Agent Research, LangGraph Adaptive RAG.

## Current System (Already Implemented)

| Component | Industry Term | Status |
|-----------|--------------|--------|
| LangGraph retrieve → grade → generate → hallucination check → rewrite | **CRAG (Corrective RAG)** | Done |
| SmartRetriever (Qdrant + Neo4j + ES fallback) | **Hybrid Multi-Strategy RAG** | Done |
| Qdrant dense+sparse with RRF fusion | **Hybrid Search** | Done |
| Neo4j entity extraction + Cypher traversal | **GraphRAG** | Done |
| FlashRank cross-encoder reranking | **Two-Stage Retrieval** | Done |
| 19-model Groq+NVIDIA NIM fallback | **Resilient Inference** | Done |
| Hallucination detection + grounding check | **Self-RAG** | Done |

The current system covers ~70% of 2026 state-of-the-art. What's missing: **adaptive routing + deep research mode**.

---

## Proposed Architecture: Dual-Mode Adaptive RAG

```
                    START
                      |
              classify_query  (query complexity router)
               /      |      \
              /       |       \
          FAST    STANDARD     DEEP
           |         |           |
        direct    [CURRENT     plan_research
        answer    PIPELINE]        |
           |         |        spawn_subagents (parallel)
           |         |             |
           |         |        synthesize
           |         |             |
           |         |          critic
           |         |             |
           v         v             v
                    END
```

---

## Mode 1: FAST (Simple Factual)

**Triggers on**: Low-complexity queries answerable from LLM knowledge or single retrieval pass.

**Examples**:
- "How many videos does channel X have?"
- "What is citizenship by investment?"

**Flow**: classify → direct LLM answer (skip retrieval/grading/hallucination)

**Latency**: <2 seconds | **LLM calls**: 1-2

---

## Mode 2: STANDARD (Current Pipeline)

**Triggers on**: Factual, comparative, and relationship queries that need document evidence.

**Examples**:
- "What does Wealthy Expat say about Dubai for crypto investors?"
- "Compare citizenship programs of Dominica vs Grenada"
- "What are the tax benefits of living in Dubai?"

**Flow**: Full existing pipeline (retrieve → grade → generate → hallucination check → citations)

**Latency**: 15-60 seconds | **LLM calls**: ~10

**No changes needed** — the current `build_youtube_rag_graph()` becomes a subgraph.

---

## Mode 3: DEEP (Multi-Agent Analytical Research)

**Triggers on**: Analytical, inferential, or pattern-finding queries requiring full corpus analysis.

**Examples**:
- "Analyze the psychological traits of Rafael Cintron based on all his videos"
- "What contradictions exist across Wealthy Expat's investment advice?"
- "What hidden assumptions does this channel never question?"
- "What topics does Rafael Cintron avoid or redirect from?"

### Deep Mode Flow

```
User Question: "Analyze psychological patterns of Rafael Cintron"
    |
    v
[PLAN] LLM decomposes into research sub-questions:
    1. "What fears or anxieties does he express across videos?"
    2. "What topics does he repeat obsessively?"
    3. "Where do his statements contradict each other?"
    4. "What emotional language patterns appear?"
    5. "What assumptions does he never question?"
    6. "What topics does he avoid or redirect from?"
    |
    v
[SPAWN SUBAGENTS] Each sub-question runs the STANDARD pipeline independently:
    Subagent 1 → retrieve + grade + generate for "fears/anxieties"
    Subagent 2 → retrieve + grade + generate for "obsessive repetition"
    Subagent 3 → retrieve + grade + generate for "contradictions"
    Subagent 4 → retrieve + grade + generate for "emotional patterns"
    Subagent 5 → retrieve + grade + generate for "unquestioned assumptions"
    Subagent 6 → retrieve + grade + generate for "avoided topics"
    (all run in PARALLEL via asyncio.gather or LangGraph Send())
    |
    v
[SYNTHESIZE] Strong LLM combines all sub-results into a coherent analysis:
    - Cross-references findings across sub-questions
    - Identifies patterns that only emerge when combining multiple angles
    - Structures the output as a research report
    |
    v
[CRITIC] LLM-as-Judge validates the synthesis:
    - Is every claim supported by sub-agent evidence?
    - Are there contradictions in the synthesis itself?
    - Did the analysis cover all sub-questions adequately?
    - Confidence score (0-1)
    |
    v
Final analytical report with citations and confidence score
```

**Latency**: 30-120 seconds | **LLM calls**: 1 (plan) + 6×~10 (subagents) + 1 (synthesize) + 1 (critic) ≈ 63

**Cost on free tier**: 63 calls ÷ 710 combined RPM (Groq+NIM) ≈ 6 seconds of API time. Actual wall time ~60s due to sequential grading within subagents.

---

## Implementation Plan

### Step 1: Query Classifier + Adaptive Router

**New node**: `classify_query` — inserted before `retrieve`

```python
class QueryClassification(BaseModel):
    mode: Literal["fast", "standard", "deep"]
    reasoning: str
    sub_questions: list[str] = []  # populated only for "deep" mode
```

**Routing via conditional edges**:
- `"fast"` → `direct_answer` → END
- `"standard"` → existing pipeline (retrieve → grade → generate → ...)
- `"deep"` → `plan_research` → `spawn_subagents` → `synthesize` → `critic` → END

**Files modified**:
- `agents/youtube.py` — new nodes, conditional edges, parent graph wrapping existing subgraph
- `schemas/state.py` — add `mode`, `sub_questions`, `sub_results`, `confidence_score`

### Step 2: Deep Research Planner + Parallel Subagents

**Planner**: LLM with structured output produces 3-8 sub-questions from the original query.

**Subagents**: Each sub-question runs the existing SmartRetriever + grading + generation pipeline. Uses LangGraph `Send()` API for dynamic fan-out (parallel execution).

**Key design**: Each subagent has isolated state — no cross-contamination between sub-question results until the synthesis step.

**Model tiering for cost optimization**:
- Planner: strong model (`llama-3.3-70b-versatile` on Groq)
- Subagent grading/generation: fast model (`llama-3.1-8b-instant` on Groq)
- Synthesizer: strong model (`llama-3.3-70b-versatile`)
- Critic: strong model (any available)

### Step 3: Synthesizer + Critic

**Synthesizer prompt**: Combines all sub-results, cross-references findings, identifies emergent patterns.

**Critic prompt**: LLM-as-Judge evaluating faithfulness, coverage, and coherence. Returns a confidence score.

**If critic rejects**: Can trigger additional subagents for under-covered areas (optional loop).

### Step 4: Episodic Memory (Advanced, Optional)

Store execution traces in Redis:
- Which query types → which modes worked best
- Which retrieval strategies returned relevant docs for which topics
- Successful query rewrites (for future reference)

Over time, the classifier gets context from past executions to make better routing decisions.

---

## State Schema Extension

```python
class YouTubeRAGState(TypedDict):
    # Existing fields (unchanged)
    question: str
    documents: list[Document]
    generation: str
    retry_count: int
    search_query: str
    grounded: bool
    citations: list[dict]
    retrieval_sources: list[str]

    # New fields for Adaptive RAG
    mode: str                      # "fast" | "standard" | "deep"
    sub_questions: list[str]       # decomposed questions (deep mode)
    sub_results: list[dict]        # results from parallel subagents
    research_plan: str             # planner's strategy description
    confidence_score: float        # critic's assessment (0-1)
```

---

## API Changes

| Endpoint | Change |
|----------|--------|
| `POST /agents/search` | Returns `mode` field in response ("fast", "standard", "deep") |
| `POST /agents/search/stream` | SSE events include mode classification and subagent progress |
| `POST /agents/search` | New optional field: `force_mode: "fast" \| "standard" \| "deep"` to override auto-classification |

---

## Why This Design

1. **Leverages everything already built** — current graph becomes a reusable subgraph
2. **No rebuild** — adds nodes around the existing pipeline, not replacing it
3. **Open source friendly** — simple queries stay fast (FAST/STANDARD); deep analysis is opt-in
4. **Scales with LLM budget** — FAST costs 1 call, STANDARD costs ~10, DEEP costs ~63
5. **Matches industry architecture** — same pattern as Anthropic's multi-agent research, OpenAI Deep Research, Gemini Deep Research

---

## References

- [Agentic RAG Survey (arXiv:2501.09136)](https://arxiv.org/abs/2501.09136)
- [Anthropic: How We Built Our Multi-Agent Research System](https://www.anthropic.com/engineering/multi-agent-research-system)
- [LangGraph Adaptive RAG](https://docs.langchain.com/oss/python/langgraph/agentic-rag)
- [LangChain Open Deep Research](https://github.com/langchain-ai/open_deep_research)
- [CoT-RAG (arXiv:2504.13534)](https://arxiv.org/abs/2504.13534)
- [HopRAG (ACL 2025)](https://aclanthology.org/2025.findings-acl.97/)
- [Next-Gen Agentic RAG with LangGraph (2026)](https://medium.com/@vinodkrane/next-generation-agentic-rag-with-langgraph-2026-edition-d1c4c068d2b8)
- [Qdrant + Neo4j GraphRAG](https://qdrant.tech/documentation/examples/graphrag-qdrant-neo4j/)
- [ByteByteGo: How OpenAI/Gemini/Claude Power Deep Research](https://blog.bytebytego.com/p/how-openai-gemini-and-claude-use)

---

*Created: 2026-04-13*
