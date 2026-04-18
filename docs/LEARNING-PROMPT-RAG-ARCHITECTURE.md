# LEARNING_PROMPT RAG Architecture — Best-in-Class 2026

> Architecture research for the pipeline:
> **one docs URL → crawl → ingest → Claude Code generates LEARNING_PROMPT-compliant study material**
>
> Goal: match or exceed current Claude-fetches-everything pipeline quality while reducing Claude token spend by ~80%, working within Sonnet 4.6 200K (post Max 5x 1M removal).
>
> Compiled: 2026-04-15
> Scope: `~/Workbench/STUDIES/LEARNING_PROMPT.md` output requirements

---

## Problem Statement

### Current pipeline pain points
- Claude Code (Opus 4.6, low effort) fetches each docs page via WebFetch
- Burns ~600K-1M Claude tokens per study, most spent on navigation decisions and re-reading
- Sonnet 4.6 1M context (previously the economical option) was removed from Max 5x plan
- Opus 4.6 low-effort still costs more per study than pre-removal Sonnet 4.6 1M high-effort
- Current throughput on Max 5x: ~1-2 studies/week (unacceptable for a "study a framework in days" target)

### Requirements (from LEARNING_PROMPT.md)
1. Read ENTIRE official documentation — complete coverage, not sampling
2. Every code block must have `# docs: <section-name>` citation
3. Do not include any class/method/import path not traceable to a specific docs page
4. Version-pinned API examples (`langchain==1.2.15`)
5. Distrust LLM-summarized intermediate layers as authoritative sources
6. Output: `summary.md` + `chapter01–08/` following strict structure
7. Target: production-competency in days, not months

---

## The 2026 Architecture Landscape

### Contenders evaluated

| Architecture | Year | Core idea | Best for |
|---|---|---|---|
| Vanilla Vector RAG | 2020 | Chunk + embed + retrieve top-K | Simple Q&A |
| **RAPTOR** | 2024 (ICLR) | Recursive hierarchical summary tree | Long-doc Q&A with hierarchy |
| **GraphRAG** (Microsoft) | 2024 | Entity graph + hierarchical community summaries | Multi-hop reasoning |
| **LightRAG** | 2024 (HKU) | Two-tier entity + theme graph | GraphRAG at lower cost |
| **HippoRAG / HippoRAG2** | 2024-25 | Biologically-inspired memory indexing | Multi-hop reasoning, evidence recall |
| **Anthropic Contextual Retrieval** | 2024 | Prepend context to chunks before embedding | Chunking accuracy (-67% errors) |
| **LazyGraphRAG** (Microsoft) | 2024 Q4 | Deferred summarization, lazy indexing | Cost-efficient GraphRAG |
| **PageIndex** (VectifyAI) | 2026 | Reasoning tree mirroring document TOC | Documentation with inherent structure |
| **ArchRAG** | 2025 | Attributed community-based hierarchical RAG | 250× token savings over GraphRAG |
| **CompactRAG** | 2026 | Offline corpus → atomic QA KB | Multi-hop, fixed-cost inference |

### Benchmark winners (BenchmarkQED, Microsoft Research 2026)
- **LazyGraphRAG**: Won 96/96 comparisons against all alternatives (vector RAG, GraphRAG, RAPTOR, vanilla) — same generative model (GPT-4o)
- **Indexing cost**: same as vector RAG, 0.1% of GraphRAG's cost
- **Query cost**: 700× lower than GraphRAG global search
- **Beats** even 1M-token context windows in win rates

### Task-specific leaders
- **RAPTOR**: highest faithfulness (70.9%) on hierarchical-structure docs
- **HippoRAG**: Evidence Recall 87.9-90.9% on multi-hop queries
- **PageIndex**: Citation-first by design, most aligned with structured-docs workflows
- **Anthropic Contextual Retrieval**: -49% retrieval errors alone, -67% with reranking

---

## Selected Architecture for LEARNING_PROMPT

**Primary: PageIndex-style Reasoning Tree**
**Optional enhancement: Anthropic Contextual Retrieval** (for semantic navigation fallback)
**Existing substrate: COELHONexus Adaptive Agentic RAG** (Qdrant + Neo4j + LangGraph)

### Why PageIndex fits this use case best

| LEARNING_PROMPT rule | RAPTOR | PageIndex |
|---|---|---|
| *"Every code block must have `# docs: <section-name>`"* | ✅ via leaf metadata | ✅ **citation is the core primitive** |
| *"Do not include any class/method not linkable to a specific docs page"* | ✅ enforceable | ✅ **architecturally enforced** |
| Distrust LLM-summarized intermediate layers | ⚠️ tree contains LLM summaries | ✅ **no summarization — just organization** |
| Preserve code fidelity | ⚠️ depends on chunking | ✅ **no embeddings, code stays pristine** |
| Complete coverage | ✅ every chunk in tree | ✅ every page in index |
| Token efficiency | ~170K/study | **~80-120K/study** |

PageIndex's "navigate the document like a human expert" mental model directly matches how LEARNING_PROMPT wants docs consumed. It does not summarize content — it organizes pointers. Citation traceability is structural, not a downstream constraint.

### Core architectural property (verification)

1. **Code preservation — HIGH confidence**
   PageIndex stores pages/chunks as references in a reasoning tree. Content is never embedded as vectors during indexing. When Claude fetches a leaf, it receives the exact raw markdown Crawl4AI scraped. There is no summarization, no compression, no encoding of code into vector space. **This is a structural guarantee, not a tuning parameter.**

2. **Token reduction — HIGH confidence for magnitude, MEDIUM for exact number**
   - 80%+ reduction vs current Opus-WebFetch pipeline: confident (eliminates navigation burn + re-reading)
   - "2 LLM calls per query" claim in PageIndex literature: true for Q&A, not for multi-chapter study synthesis
   - For a full LEARNING_PROMPT study (8 chapters), expect ~20-40 Claude calls totaling ~80-120K tokens — still ~80% less than current pipeline
   - Real number needs measurement on first real ingestion

3. **Best-in-class for this workflow — MEDIUM confidence**
   - Strongest match to LEARNING_PROMPT requirements among 2026 options surveyed
   - Based on vendor/research benchmarks (Microsoft BenchmarkQED, PageIndex papers, Anthropic benchmarks) — not independent reruns
   - Risk: docs-site variance (Docusaurus vs MDX vs custom SPA) may require per-site parser tuning

---

## Target Architecture

```
Input: single URL (e.g., https://docs.langchain.com) + library name + version tag

┌─────────────────────────────────────────────────────────────────────┐
│  Phase 1: INGESTION (async, one-time per library/version)           │
│                                                                     │
│  Crawl4AI (self-hosted module) or Playwright fallback               │
│       │                                                             │
│       ▼                                                             │
│  Raw markdown per page → MinIO (bucket: docs-raw)                   │
│       │                                                             │
│       ▼                                                             │
│  TOC/Sidebar parser (per-site: Docusaurus, MkDocs, Sphinx, SPA)     │
│       │                                                             │
│       ▼                                                             │
│  PageIndex Reasoning Tree builder (1-2 LLM calls via Ollama/NIM)    │
│       │                                                             │
│       ▼                                                             │
│  Postgres:                                                          │
│    doc_pages (url, section_path, raw_markdown, version, crawled_at) │
│    code_blocks (page_id, language, raw_text, line_range)            │
│    tree_nodes (parent_id, page_ref, summary?, level, position)      │
│                                                                     │
│  Optional: Anthropic Contextual Retrieval chunking                  │
│    → Qdrant (for semantic cross-reference, not primary retrieval)   │
│                                                                     │
│  Optional: Neo4j entity graph                                       │
│    (Class)-[hasMethod]->(Method)-[takesParam]->(Param)              │
│    (extracted via NIM free tier)                                    │
└─────────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────────┐
│  Phase 2: SERVING (on demand, per study generation)                 │
│                                                                     │
│  FastAPI endpoints:                                                 │
│    GET  /study/libraries                        → installed libs    │
│    POST /study/ingest     {url, library}        → triggers Phase 1  │
│    GET  /study/tree?library=X                   → reasoning tree    │
│    GET  /study/page?library=X&path=...          → verbatim markdown │
│    GET  /study/code?library=X&path=...          → code blocks only  │
│    GET  /study/find?library=X&term=...          → Neo4j relations   │
│                                                                     │
│  MCP server wrapping these endpoints → Claude Code                  │
└─────────────────────────────────────────────────────────────────────┘

┌─────────────────────────────────────────────────────────────────────┐
│  Phase 3: STUDY GENERATION (Claude Code orchestrates)               │
│                                                                     │
│  1. study_tree(library)               → ~2K tokens                  │
│  2. plan chapters based on tree                                     │
│  3. for each chapter (01-08):                                       │
│       relevant_nodes = pick from tree                               │
│       for each node:                                                │
│         markdown = study_page(node.path)   → verbatim               │
│         codes   = study_code(node.path)    → verbatim               │
│       generate chapter with # docs: citations                       │
│  4. write files via Write tool                                      │
│                                                                     │
│  Total Claude tokens: ~80-120K input + ~80K output per full study   │
└─────────────────────────────────────────────────────────────────────┘
```

---

## Token Budget Comparison

Target: generate complete LEARNING_PROMPT study for one framework (~1M tokens raw docs).

| Pipeline | Model | Context | Input tokens | Throughput on Max 5x |
|---|---|---|---|---|
| Pre-change (removed) | Sonnet 4.6 1M high-effort | 1M | ~600K | 3-5 studies/week |
| Current (painful) | Opus 4.6 1M low-effort | 1M | ~600K-1M | 1-2 studies/week |
| **PageIndex + Sonnet 4.6** | Sonnet 4.6 200K high-effort | 200K | **~80-120K** | **6-10 studies/week** |
| RAPTOR + Sonnet 4.6 | Sonnet 4.6 200K | 200K | ~170K | 4-6 studies/week |
| LazyGraphRAG | Sonnet 4.6 200K | 200K | ~150-200K | 3-5 studies/week |

**PageIndex recovers and exceeds pre-change throughput using only the model still in the Max 5x plan.**

---

## LEARNING_PROMPT Compliance

| Rule | Compliance mechanism |
|---|---|
| Read ENTIRE official documentation | Crawler fetches every page in sitemap; tree contains every page |
| `# docs: <section-name>` on every code block | `doc_pages.section_path` stored at ingestion, emitted in generation |
| No class/method without docs page link | Structural: Claude can only reference content from tree leaves |
| Tier 3 Python introspection | **UNCHANGED** — agent still runs `python -c "help(...)"` per rule. PageIndex augments T1, not T3. |
| Version pinning | Crawler captures version tag; tree is version-scoped |
| Migration guide coverage | Crawler fetches `/migration`, `/upgrading`, `/changelog` paths; stored as tree branches |
| Changelog (last 12 months) | Crawler picks up GitHub releases page separately |
| No padding, code-first, LEARNING_PROMPT structure | Claude's writing task, unchanged |

---

## Integration with Existing COELHONexus Assets

### Reused (no changes)
- `services/ingestion.py` → adapt for docs source
- `services/chunker.py` → optional chunking for Contextual Retrieval layer
- `services/embeddings.py` → NIM `nv-embedcode-7b-v1` for code chunks
- `services/graph_builder.py` → entity extraction pattern (Class/Method/Param)
- `services/retriever.py` → SmartRetriever for cross-library queries
- `services/reranker.py` → FlashRank, used optionally
- `services/cache.py` → Redis for tree caching
- LangGraph Adaptive RAG (FAST/STANDARD/DEEP modes)
- Qdrant (with dense + sparse hybrid)
- Neo4j
- Postgres
- MinIO
- NIM free tier (embeddings + entity extraction)
- Ollama Qwen3-30B-A3B (local summarization if needed)

### New (~1500-2000 lines total)
```
apps/fastapi/
├── services/
│   ├── docs_crawler.py         # Crawl4AI wrapper
│   ├── docs_toc_parser.py      # per-site TOC extractors (Docusaurus, MkDocs, Sphinx, SPA)
│   ├── pageindex_builder.py    # reasoning tree construction
│   ├── docs_indexer.py         # insert into Postgres + Qdrant + Neo4j
│   └── docs_serve.py           # tree + page + code retrieval
├── routers/
│   └── study.py                # FastAPI endpoints
├── agents/
│   └── study_generator.py      # optional: LangGraph chapter orchestrator
└── tasks/
    └── ingest_library.py       # Celery task: URL → full ingestion
```

### Infrastructure (COELHOCloud)
- `module.crawl4ai` — new Terraform module, ~100 lines, pattern like `module.searxng`
- Optional: `module.mcp-coelhonexus-study` — MCP server exposing FastAPI endpoints

---

## Implementation Roadmap

### MVP (1-2 days) — validate end-to-end
1. Crawl4AI → scrape one mid-sized Docusaurus docs site (e.g., `docs.llamaindex.ai`)
2. TOC parser for Docusaurus only
3. Minimal PageIndex tree builder (Ollama Qwen3-30B for tree structuring, ~200 lines)
4. Postgres tables: `doc_pages`, `code_blocks`, `tree_nodes`
5. FastAPI: 4 endpoints (`tree`, `page`, `code`, `ingest`)
6. MCP server wrapping them
7. Run one LEARNING_PROMPT study end-to-end, measure actual token usage

**Success criteria:**
- Generated study covers every major docs section (spot check vs sidebar)
- Every code block has valid `# docs:` citation (grep verification)
- Total Claude input tokens < 200K (cloudwatch / anthropic dashboard)
- Produces LEARNING_PROMPT-compliant `summary.md` + `chapter01-08`

### Phase 2 (if MVP validates) — production hardening
- Per-site TOC parsers: MkDocs, Sphinx, VitePress, custom SPAs
- Optional: Anthropic Contextual Retrieval for chunks that don't map cleanly to tree nodes
- Neo4j entity graph for cross-library relationships
- Airflow DAG for scheduled re-ingestion (changelog monitoring)
- Caching strategy for tree + page lookups (Redis)
- OpenTelemetry observability (per existing `opentelemetry-and-ai-agents-guide.md`)
- Evaluation harness: RAGAS for citation faithfulness + coverage metrics

### Phase 3 (optional) — cross-library queries
- Extend SmartRetriever to span multiple library tree indexes
- Enable queries like *"how do FastAPI + Pydantic + SQLModel fit together"*
- Neo4j becomes primary retrieval substrate
- Adaptive RAG DEEP mode spawns sub-agents per library

---

## Honest Caveats

### What could go wrong
1. **Docs site variance** — Crawl4AI + per-site TOC parser may miss pages on unusual layouts. Mitigation: crawler respects sitemap.xml; unknown sites fall back to BFS crawl from root.
2. **Tree quality depends on structural signal** — sites without clean hierarchy (e.g., marketing pages mixed with reference) produce poorer trees. Fallback: RAPTOR-style semantic clustering for flat docs.
3. **"Best architecture" is a 2026-snapshot claim** — the field moves fast; expect to revisit in 6 months. Architecture is designed for easy swapping of the tree construction strategy (RAPTOR, LazyGraphRAG, PageIndex all implementable behind the same serving API).
4. **PageIndex is newer than RAPTOR/GraphRAG** — less battle-tested in production. Risk mitigation: start with MVP on one library, measure, iterate.
5. **Token savings require discipline** — if Claude Code agent reverts to its WebFetch habits, savings evaporate. Enforce via `~/.claude/CLAUDE.md` explicit preference for study MCP tools.

### What independent verification would improve confidence
- End-to-end token measurement on first real library ingestion
- Citation accuracy spot-check (random 20 citations → verify URL + section match)
- Coverage spot-check (sidebar pages vs tree pages diff)
- A/B comparison with current Opus-fetch pipeline on same framework

---

## Decision Log

| Decision | Alternative considered | Reason chosen |
|---|---|---|
| PageIndex over RAPTOR | RAPTOR (ICLR 2024 SOTA) | PageIndex preserves citation traceability (LEARNING_PROMPT core rule) without LLM-summarized intermediate layer |
| PageIndex over LazyGraphRAG | LazyGraphRAG (96/96 BenchmarkQED) | LazyGraphRAG optimizes for graph queries; PageIndex optimizes for documentation hierarchy |
| Crawl4AI over Firecrawl | Firecrawl (more mature SaaS) | Apache 2.0 license, Python-native (fits Airflow DAGs), lighter infrastructure (no Postgres/Redis needed) |
| Self-hosted over Cloudflare /crawl | Cloudflare free tier | Free tier caps at ~150 pages/day — insufficient for aggressive study volume |
| Sonnet 4.6 200K over Opus 4.6 1M | Opus 4.6 1M (current pipeline) | After PageIndex reduces context need, 200K is sufficient; Sonnet is cheaper per token |
| Keep existing Qdrant/Neo4j | Drop them | Optional for cross-library queries; no cost to keeping them |

---

## Sources

- [PageIndex vs Traditional RAG — Analytics Vidhya 2026](https://www.analyticsvidhya.com/blog/2026/03/pageindex-vs-rag-document-chatbots/)
- [LazyGraphRAG — Microsoft Research](https://www.microsoft.com/en-us/research/blog/lazygraphrag-setting-a-new-standard-for-quality-and-cost/)
- [BenchmarkQED — Microsoft 2026 automated RAG benchmark](https://www.microsoft.com/en-us/research/blog/benchmarkqed-automated-benchmarking-of-rag-systems/)
- [Anthropic — Contextual Retrieval](https://www.anthropic.com/news/contextual-retrieval)
- [RAPTOR: Recursive Abstractive Processing (ICLR 2024)](https://arxiv.org/abs/2401.18059)
- [Microsoft GraphRAG](https://microsoft.github.io/graphrag/)
- [LightRAG](https://www.lettria.com/blogpost/what-is-lightrag-definition-core-approaches-and-examples)
- [GraphRAG 2026 Practitioner's Guide](https://medium.com/graph-praxis/graph-rag-in-2026-a-practitioners-guide-to-what-actually-works-dca4962e7517)
- [Cutting GraphRAG Token Costs 90% — Graph Praxis, Mar 2026](https://medium.com/graph-praxis/cutting-graphrag-token-costs-by-90-in-production-5885b3ffaef0)
- [Awesome-GraphRAG (DEEP-PolyU)](https://github.com/DEEP-PolyU/Awesome-GraphRAG)
- [ArchRAG — 250× token savings](https://arxiv.org/html/2502.09891)
- [CompactRAG — token-efficient multi-hop](https://arxiv.org/html/2602.05728v1)
- Internal: [ADAPTIVE-RAG-ARCHITECTURE.md](./ADAPTIVE-RAG-ARCHITECTURE.md)
- Internal: [AGENTIC-RAG-ARCHITECTURE.md](./AGENTIC-RAG-ARCHITECTURE.md)
- Internal: [RAG-2026-STATE-OF-THE-ART.md](./RAG-2026-STATE-OF-THE-ART.md)
- Internal: [NVIDIA-NIM-EMBEDDING-MODELS.md](./NVIDIA-NIM-EMBEDDING-MODELS.md)
- Target output: [~/Workbench/STUDIES/LEARNING_PROMPT.md](../../STUDIES/LEARNING_PROMPT.md)
