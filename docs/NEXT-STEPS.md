# Next Steps — COELHONexus Adaptive Agentic RAG

> Prioritized roadmap after the Adaptive RAG implementation (2026-04-14).

## Completed

| Feature | Status |
|---------|--------|
| Adaptive RAG (FAST / STANDARD / DEEP) with auto-routing | Done |
| Groq-first 19-model supermodel (5 Groq + 14 NVIDIA NIM) | Done |
| Neo4j retriever fix (valueType for array entity IDs) | Done |
| top_k=15 two-stage retrieval (overfetch → FlashRank rerank → top 10) | Done |
| Auto channel scope detection (no cross-channel contamination) | Done |
| Think tag stripping from model reasoning tokens | Done |
| Vitoria Stecca channel ingested (55 videos → ES + Qdrant + Neo4j) | Done |
| Wealthy Expat channel (402 videos → ES + Qdrant + Neo4j) | Done |
| Celery background tasks (crawling, Qdrant ingestion, Neo4j graph extraction) | Done |
| Flower monitoring dashboard on COELHOCloud | Done |
| Redis cache with mode-aware keys | Done |
| SSE streaming for all 3 modes | Done |

## Next Steps

### Priority 1 — Quick Wins (minutes)

**Qdrant `is_tenant` payload index**
- One-time command to create a keyword index on `channel_id` with `is_tenant=True`
- Co-locates same-channel vectors on disk for cache efficiency
- Makes filtered searches 2-3x faster
- No re-ingestion needed

**Clean Neo4j duplicates**
- The duplicate graph ingestion task created extra entities before cancellation
- Run entity resolution once: `resolve_entities(neo4j_graph)`
- ~1 minute, no LLM cost

### Priority 2 — Conversation Memory

**Fix AsyncRedisSaver deadlock**
- Currently disabled: `workflow.compile()` without checkpointer
- Root cause: checkpointer initialized inside lifespan `async with` block causes deadlock when called from endpoint handlers
- Fix: initialize checkpointer outside the context manager or use a separate Redis connection
- Enables: follow-up questions ("tell me more about that"), conversation history per thread_id

### Priority 3 — Web Frontend

**Go + HTMX + Templ search UI**
- Search bar → hits `/agents/search/stream` SSE endpoint
- Real-time node-by-node progress (classify → retrieve → grade → generate)
- Mode indicator (FAST/STANDARD/DEEP)
- Citations with clickable YouTube links
- Channel selector for manual scope filtering
- Already have `coelhonexus-web` pod running (Go app)

### Priority 4 — Map-Reduce Full Corpus Scan

**Extends DEEP mode for "analyze ALL videos" queries**
- Current DEEP: 8 subagents × top_k=15 = ~120 chunks analyzed
- Map-Reduce: processes ALL 457 transcripts through focused extraction prompts
- Map phase: cheap fast model (Groq `llama-3.1-8b-instant` at 840 TPS)
- Reduce phase: strong model synthesizes all findings
- Cost: ~457 LLM calls ÷ 710 combined RPM ≈ 40 seconds
- Runs as Celery background task for long analyses

### Priority 5 — Observability

**Langfuse integration**
- Track: query latency, token costs per model, retrieval source distribution, grading pass rate, hallucination rate, cache hit rate
- Compare model quality across the 19-model fallback chain
- Identify which queries trigger DEEP mode most often
- Dashboard for RAG quality metrics over time

### Priority 6 — Open Source Preparation

**Portfolio-ready release**
- README with architecture diagrams
- Quick start guide (Docker Compose for local dev)
- API documentation (auto-generated from FastAPI /docs)
- Example queries and expected outputs
- LICENSE (MIT or Apache 2.0)
- Demo video or GIF showing FAST/STANDARD/DEEP modes
- Blog post: "Building an Adaptive Agentic RAG with LangGraph, Neo4j, and Qdrant"

## Future Enhancements

| Enhancement | Impact | Priority |
|-------------|--------|----------|
| GraphRAG community detection (Leiden/Louvain on Neo4j) | Pre-computed topic clusters for global queries | Medium |
| RAPTOR tree summaries (multi-level abstractions in Qdrant) | Better analytical query quality | Medium |
| Enriched KG with sentiment/stance extraction | Enables psychological analysis queries | Medium |
| Multi-hop Neo4j traversal (2-3 hops instead of 1) | Deeper relationship queries | Low |
| Episodic memory (learns from past query execution traces) | System improves over time | Low |
| PostgreSQL checkpointer (replaces Redis for persistence) | Survives Redis flushes | Low |
| Celery Beat scheduled ingestion (auto-detect new videos) | Automated data freshness | Low |
| Prometheus + Grafana monitoring | Production observability | After launch |

---

*Created: 2026-04-14*
