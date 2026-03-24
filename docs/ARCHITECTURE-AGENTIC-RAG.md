# Agentic Hybrid RAG Architecture (2026)

> State-of-the-art architecture for YouTube Content Search and future RAG/GraphRAG projects.

## Overview

This architecture combines:
- **Agentic RAG** with LangGraph (self-correcting retrieval)
- **Hybrid Retrieval** using Qdrant (vectors) + Neo4j (graph)
- **GraphRAG** for entity relationships and multi-hop reasoning

### Performance Benchmarks

| Metric | Traditional RAG | This Architecture |
|--------|-----------------|-------------------|
| Accuracy | Baseline | **+20-25%** |
| Latency | 300-500ms | **<200ms** |
| Scale | 10M vectors | **100M+ vectors** |
| Precision (complex queries) | ~70% | **Up to 99%** |

---

## System Architecture

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                              USER QUERY                                     │
└─────────────────────────────────────────────────────────────────────────────┘
                                      │
                                      ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                         LANGGRAPH AGENTIC RAG                               │
│  ┌─────────────────────────────────────────────────────────────────────┐    │
│  │                                                                     │    │
│  │   ┌──────────┐    ┌──────────────┐    ┌──────────┐                 │    │
│  │   │ RETRIEVE │───▶│ GRADE DOCS   │───▶│ GENERATE │                 │    │
│  │   └──────────┘    └──────────────┘    └──────────┘                 │    │
│  │        ▲                 │                  │                       │    │
│  │        │                 ▼                  ▼                       │    │
│  │        │          ┌──────────────┐   ┌───────────────┐             │    │
│  │        └──────────│REWRITE QUERY │   │HALLUCINATION  │             │    │
│  │                   └──────────────┘   │   CHECK       │             │    │
│  │                                      └───────────────┘             │    │
│  └─────────────────────────────────────────────────────────────────────┘    │
└─────────────────────────────────────────────────────────────────────────────┘
                                      │
                    ┌─────────────────┴─────────────────┐
                    ▼                                   ▼
┌─────────────────────────────────┐   ┌─────────────────────────────────┐
│           QDRANT                │   │            NEO4J                │
│      (Vector Search)            │   │       (Knowledge Graph)         │
├─────────────────────────────────┤   ├─────────────────────────────────┤
│ - Chunk embeddings              │   │ - Entities (Video, Channel,    │
│ - Semantic similarity           │   │   Topic, Speaker, Segment)     │
│ - Sub-200ms latency             │   │ - Relationships                │
│ - 100M+ vectors scale           │   │ - Community summaries          │
│ - Metadata filtering            │   │ - Graph traversal              │
└─────────────────────────────────┘   └─────────────────────────────────┘
```

---

## Component Responsibilities

### Qdrant (Vector Database)
- **Purpose**: Fast semantic similarity search
- **Stores**: Chunk embeddings, metadata
- **Latency**: Sub-200ms
- **Scale**: 100M+ vectors
- **Use for**: "Find similar content" queries

### Neo4j (Graph Database)
- **Purpose**: Relationship traversal, entity connections
- **Stores**: Entities, relationships, community summaries
- **Use for**: Multi-hop reasoning, "How is X related to Y?" queries

### LangGraph (Agent Orchestration)
- **Purpose**: Self-correcting retrieval workflow
- **Features**:
  - Document relevance grading
  - Query rewriting on poor results
  - Hallucination checking
  - Cyclic retry logic

---

## Data Flow

### Ingestion Pipeline

```
YouTube Video
      │
      ▼
┌─────────────────────────────────────────┐
│ INGESTION LAYER                         │
│ - Fetch transcript + metadata           │
│ - Extract video frames (optional)       │
│ - Transcribe audio if needed            │
└─────────────────────────────────────────┘
      │
      ▼
┌─────────────────────────────────────────┐
│ PROCESSING LAYER                        │
│ - Semantic chunking (512-token windows) │
│ - Sliding overlap (50-100 tokens)       │
│ - Entity extraction (LLM)               │
└─────────────────────────────────────────┘
      │
      ├──────────────────┬────────────────────┐
      ▼                  ▼                    ▼
┌──────────────┐  ┌──────────────┐  ┌──────────────┐
│   QDRANT     │  │    NEO4J     │  │    NEO4J     │
│  (Vectors)   │  │  (Entities)  │  │  (Metadata)  │
│              │  │              │  │              │
│ - Embeddings │  │ - Nodes      │  │ - Timestamps │
│ - Chunks     │  │ - Relations  │  │ - Tags       │
└──────────────┘  └──────────────┘  └──────────────┘
```

### Query Pipeline

```
User Query
      │
      ▼
┌─────────────────────────────────────────┐
│ 1. METADATA FILTER (Neo4j)              │
│    - Filter by: timestamp, channel, tags│
└─────────────────────────────────────────┘
      │
      ▼
┌─────────────────────────────────────────┐
│ 2. PARALLEL RETRIEVAL                   │
│    ├─ Semantic Search (Qdrant) ─────┐   │
│    └─ Graph Traversal (Neo4j) ──────┤   │
│                                     ▼   │
│              Result Fusion & Rerank     │
└─────────────────────────────────────────┘
      │
      ▼
┌─────────────────────────────────────────┐
│ 3. DOCUMENT GRADING (LLM)               │
│    - Relevance scoring                  │
│    - Filter threshold: 0.7              │
└─────────────────────────────────────────┘
      │
      ├─── Good docs ───▶ GENERATE
      │
      └─── No good docs ─▶ REWRITE QUERY ─▶ RETRY
```

---

## Chunking Strategy

| Setting | Value | Rationale |
|---------|-------|-----------|
| Chunk size | 512 tokens | Full context capture |
| Overlap | 50-100 tokens | Boundary preservation |
| Method | Semantic + sliding window | Best for conversational content |

> **Note**: Embedding model quality matters MORE than chunking strategy.

---

## Knowledge Graph Schema

### Nodes

```cypher
(:Video {
  id: string,
  title: string,
  channel_id: string,
  upload_date: datetime,
  duration_minutes: int,
  view_count: int,
  embedding: list<float>
})

(:Channel {
  id: string,
  name: string,
  subscriber_count: int
})

(:Topic {
  name: string,
  description: string
})

(:Segment {
  id: string,
  start_timestamp: string,
  end_timestamp: string,
  text: string,
  embedding: list<float>
})

(:Speaker {
  id: string,
  name: string
})
```

### Relationships

```cypher
(Video)-[:BELONGS_TO]->(Channel)
(Video)-[:MENTIONS {weight: float}]->(Topic)
(Segment)-[:PART_OF]->(Video)
(Segment)-[:CONTAINS]->(Topic)
(Segment)-[:PRECEDES]->(Segment)
(Topic)-[:RELATED_TO {similarity: float}]->(Topic)
(Speaker)-[:SPEAKS_IN]->(Segment)
```

---

## LangGraph Agent Implementation

```python
from typing import TypedDict, List
from langgraph.graph import StateGraph, END
from langchain_core.documents import Document

class YouTubeSearchState(TypedDict):
    question: str
    documents: List[Document]
    generation: str
    retry_count: int

def hybrid_retrieve(state: YouTubeSearchState):
    """Parallel retrieval from Qdrant + Neo4j."""
    # 1. Vector search (Qdrant)
    vector_results = qdrant.search(embed(state["question"]), limit=20)

    # 2. Graph traversal (Neo4j)
    entities = extract_entities(state["question"])
    graph_results = neo4j.query("""
        MATCH (e:Topic)<-[:MENTIONS]-(v:Video)-[:BELONGS_TO]->(c:Channel)
        WHERE e.name IN $entities
        RETURN v, c
    """, entities=entities)

    # 3. Fusion
    documents = fuse_and_rerank(vector_results, graph_results)
    return {"documents": documents}

def grade_documents(state: YouTubeSearchState):
    """LLM evaluates document relevance."""
    graded = []
    for doc in state["documents"]:
        score = llm.grade_relevance(state["question"], doc)
        if score > 0.7:
            graded.append(doc)
    return {"documents": graded}

def generate_with_citations(state: YouTubeSearchState):
    """Generate answer with video timestamp citations."""
    prompt = f"""Answer based on these video transcripts:

{format_docs(state["documents"])}

Question: {state["question"]}

Include citations as: [Concept] (Video: title, timestamp)
"""
    return {"generation": llm.invoke(prompt)}

def check_hallucination(state: YouTubeSearchState):
    """Verify answer is grounded in documents."""
    is_grounded = llm.verify_grounding(
        state["generation"],
        state["documents"]
    )
    return {"grounded": is_grounded}

def rewrite_query(state: YouTubeSearchState):
    """Expand query with synonyms and related terms."""
    new_query = llm.rewrite_query(state["question"])
    return {"question": new_query, "retry_count": state["retry_count"] + 1}

def decide_next(state: YouTubeSearchState):
    if state["documents"] and len(state["documents"]) > 0:
        return "generate"
    elif state["retry_count"] < 3:
        return "rewrite"
    else:
        return "fail"

# Build graph
workflow = StateGraph(YouTubeSearchState)
workflow.add_node("retrieve", hybrid_retrieve)
workflow.add_node("grade", grade_documents)
workflow.add_node("generate", generate_with_citations)
workflow.add_node("check", check_hallucination)
workflow.add_node("rewrite", rewrite_query)

workflow.set_entry_point("retrieve")
workflow.add_edge("retrieve", "grade")
workflow.add_conditional_edges("grade", decide_next, {
    "generate": "generate",
    "rewrite": "rewrite",
    "fail": END
})
workflow.add_edge("generate", "check")
workflow.add_conditional_edges("check", lambda s: "end" if s.get("grounded") else "rewrite", {
    "end": END,
    "rewrite": "rewrite"
})
workflow.add_edge("rewrite", "retrieve")

app = workflow.compile()
```

---

## Tech Stack

| Component | Technology | Why |
|-----------|------------|-----|
| Vector DB | Qdrant | Sub-200ms, 100M+ scale |
| Graph DB | Neo4j | Mature ecosystem, vector support |
| Embedding | bge-base-en-v1.5 (local) or Gemini (API) | Best quality/cost |
| LLM | NVIDIA NIM / Claude | Free tier / quality |
| Agent Framework | LangGraph | Cyclic graphs, state management |
| API | FastAPI | Async, modern |
| Checkpointing | Redis | LangGraph persistence |

---

## Implementation Phases

### Phase 1: Agentic RAG Baseline
- [ ] LangGraph workflow with cyclic retry
- [ ] Document grader (LLM-based)
- [ ] Query rewriter
- [ ] Basic Qdrant integration

### Phase 2: Hybrid Retrieval
- [ ] Neo4j graph schema
- [ ] Parallel retrieval (Qdrant + Neo4j)
- [ ] Result fusion and reranking

### Phase 3: Knowledge Graph
- [ ] Entity extraction (LLMGraphTransformer)
- [ ] Relationship mapping
- [ ] Community detection

### Phase 4: Production Hardening
- [ ] Hallucination checking
- [ ] Citation generation with timestamps
- [ ] Caching layer
- [ ] Monitoring and evaluation

### Phase 5: Multi-modal (Optional)
- [ ] Video frame extraction
- [ ] Visual description generation
- [ ] Unified embeddings

---

## File Structure

```
apps/fastapi/
├── services/
│   ├── retriever.py        # HybridRetriever (Qdrant + Neo4j)
│   ├── ingestion.py        # Dual ingestion pipeline
│   ├── chunker.py          # Semantic chunking
│   └── grader.py           # Document relevance grader
├── agents/
│   └── youtube_search.py   # LangGraph agentic workflow
├── schemas/
│   ├── graph.py            # Neo4j node/relationship models
│   └── state.py            # LangGraph state definitions
└── routers/v1/youtube/
    ├── search.py           # Search endpoints
    ├── ingest.py           # Video ingestion endpoint
    └── agents.py           # Agent configuration
```

---

## References

- [GraphRAG with Qdrant and Neo4j](https://qdrant.tech/documentation/examples/graphrag-qdrant-neo4j/)
- [Neo4j + Qdrant RAG Pipeline](https://neo4j.com/blog/developer/qdrant-to-enhance-rag-pipeline/)
- [Lettria: 20-25% Accuracy Gains](https://qdrant.tech/blog/case-study-lettria-v2/)
- [Agentic RAG with LangGraph](https://docs.langchain.com/oss/python/langgraph/agentic-rag)
- [A-RAG: 5-13% Accuracy Improvement](https://arxiv.org/html/2602.03442v1)
- [Microsoft GraphRAG](https://github.com/microsoft/graphrag)
- [RAG Architectures 2026](https://www.techment.com/blogs/rag-architectures-enterprise-use-cases-2026/)
- [Vector vs Graph RAG](https://optimumpartners.com/insight/vector-vs-graph-rag-how-to-actually-architect-your-ai-memory/)

---

*Last updated: 2026-03-23*
