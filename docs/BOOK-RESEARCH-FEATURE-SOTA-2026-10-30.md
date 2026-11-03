# Third feature: Book Distiller (decision + architecture, saved 2026-10-30; named 2026-11-02)

Question: which third Nexus feature maximizes the chance of a Senior AI Engineer job in Abu Dhabi (from Brazil): Deep Research only, Book Distillation only, or both combined? Stack: DeepAgents + LangChain + LangGraph + FastMCP. Private leverage: COELHO Search MCP + LLM Rotator ($0 for the author, paid providers for everyone else).

Status: decision + design only. Nothing built. The COELHO Search MCP failed to connect (timeout) during this research, so the web-researcher wiring is untested.

**Update 2026-11-02: named Book Distiller, and it is the third feature again.** The feature (book distillation + deep research over the distilled book and the live web) is called **Book Distiller**, the sibling of Docs Distiller; the name was chosen after a collision check (Knowledge Distiller reads as the ML model-compression technique; Scriptorium, Marginalia and Tome are taken in the AI space). Domain code `bd`: `domains/bd/`, `/api/v1/bd`, Celery queue `bd`. The user builds it by hand, to learn Deep Agents, LangChain, LangGraph and FastMCP. Tagline: "Distill any book, then research across it and the live web, with citations." The file name keeps "BOOK-RESEARCH" because it is dated and linked from elsewhere; read "Book Research" below as Book Distiller.

---

## 1. Decision

**Build the combined option, as ONE product with the research agent at the centre.** Book distillation is the ingestion/indexing stage; the deep-research agent works over the distilled book plus the live web.
| Option | Verdict | Why |
|---|---|---|
| A. Deep Research only | Reject as headline | Most crowded space. LangChain archived `open_deep_research` (2026-08-21); DeerFlow ~75k stars; LangChain sells Managed Deep Agents with built-in search. The $0 search/LLM edge is invisible to a recruiter. |
| B. Book Distillation only | Reject | Thin summarizer, many clones (`ebook-to-mindmap` ~1.2k stars, `condensr`, `book-to-skill`), low senior signal. |
| **C. Combined, research-first** | **Pick** | Exercises what Abu Dhabi postings actually list: chunking, hybrid search, reranking, citations, retrieval evals, supervisor-worker fan-out, state compression, cross-document reconciliation, hallucination verification, observability. Reuses Docs Distiller planner/synth patterns. Little direct competition. |
| C-lite (web research, books as one source type) | Fallback | Ships faster but loses the structured-distillation story. Use only if time-boxed. |

Honest limit: no posting asks for "book distillation", and the hiring evidence is aggregator-only. The feature is a vehicle to show the skills above; deployment, evals and observability matter as much as the feature (the Open Innovation AI JDs skew infra/K8s/ArgoCD).

---

## 2. Version baseline (verified 2026-10-30)

| Package | Version | Date | Note |
|---|---|---|---|
| `deepagents` | 0.7.32 | 2026-10-23 | Async subagents (0.7.30: queued follow-ups, `notify_parent`, `webhook`); planning (`write_todos`) is opt-in since 0.7 via `TodoListMiddleware`; no breaking changes listed. |
| `langchain-core` | 1.7.0 | 2026-10-22 | **Breaking**: tool-call args stream as `tool-call-delta`. Upgrade clients first (Python `langchain-core>=1.6.13`), then server; or pin `<1.7.0`. |
| `langchain` | 1.4.x (1.4.6 on 2026-10-14) | | `HumanInTheLoopMiddleware(interrupt_mode="per_call")`; per-call becomes the default in the next major. |
| MCP in LangChain | `langchain.mcp` (beta) | 2026-09-01 (1.4.0) | `MultiServerMCPClient` replaced by `MCPAdapter`; `langchain-mcp-adapters` deprecated. Install `langchain[mcp]`. |
| `langgraph` | 1.2.0 per changelog page (2026-05-12) | | Unverified as current; check PyPI. |
| FastMCP | 4.1.4 stable | 2026-10-26 | 4.2.0a1 (skills) is pre-release. 3.4.11 still maintained. 4.x breaking notes: 4.1.0 (HTTP sessions expire after 30 min idle, regex search engine, MultiAuth client IDs), 4.1.2 (OAuth `issuer` must be set), 4.1.4 (`py-key-value-aio[keyring]`). **Check which FastMCP Nexus runs before building.** |

Managed Deep Agents: LangChain blog says public beta since 2026-08-07, docs say private beta/CLI-first. Do not depend on it; self-host with the SDK (needed for a custom backend and your own LLM endpoint anyway).

---

## 3. Architecture

```
upload (PDF|EPUB) ─► 1 INGEST ─► 2 DISTILL ─► 3 INDEX ─► 4 RESEARCH (Deep Agent) ─► report + citations
                     LangGraph    LangGraph    Qdrant      supervisor + subagents        │
                     (determin.)  map-reduce   (+Neo4j?)   book-reader │ web-researcher  │
                                  Send fan-out hybrid      verifier │ writer             ▼
                                                           └── FastMCP Book server ◄── 5 EVALS / TRACES
```

### 1. Ingest (deterministic LangGraph graph, not an agent)
- EPUB: parse natively (spine + nav TOC). No OCR.
- PDF: bookmarks/TOC first; LLM heading refinement as fallback. HiPS (arXiv 2509.00909): TOC-based is strongest when metadata is complete, LLM-refined improves heading precision and deep levels.
- Parser: Docling (MIT) default; MinerU for formula-heavy books; Marker also reads EPUB but weights need a paid licence above $5M revenue. Published comparisons are vendor-run: benchmark all three on 3 of your own books (heading recovery vs TOC, summary quality).
- Output: chapter/section tree with breadcrumbs, stored in MinIO.

### 2. Distill (LangGraph map-reduce, `Send` fan-out)
- Summarize section → chapter → book; each merge step receives source context (Ou & Lapata, arXiv 2502.00977, reduces hallucination in hierarchical merging).
- Prepend section breadcrumbs to every prompt.
- Extract claims, concepts, glossary; every item carries a page/location locator.
- Verify pass: re-check each claim against its source span.

### 3. Index
- Hybrid dense + BM25 over chunks AND summary-tree nodes. The TOC gives a RAPTOR-style tree for free (no clustering cost).
- Default: no GraphRAG. arXiv 2604.09666: agentic search over dense RAG narrows the gap; GraphRAG keeps an edge on complex multi-hop but has high offline cost. Add a Neo4j concept graph only if multi-hop book questions fail the evals.

### 4. Research (`create_deep_agent`)
- Supervisor + subagents: `book-reader` (Book MCP tools), `web-researcher` (COELHO Search MCP tools), `verifier`, `writer`.
- Async subagents for parallel fan-out; `interrupt_on` for plan approval; report written to the virtual filesystem; report formats as Skills; MCP tools passed through `tools=`.
- Citation rule: every claim cites a book locator or a URL.
- Verifier: (a) every cited source must appear in the retrieval log (a citation absent from the log is fabricated by definition), then (b) LLM entailment on the cited span. Planner-executor separation is the common production pattern; vendor-documented, not independently benchmarked.
- Known production failure: citation accuracy degrades when the writer gets a large evidence buffer plus a long output target, so write section-by-section from per-section evidence, not one pass.

### 5. FastMCP Book server
- Tools: `ingest_book`, `search_book`, `read_section`, `get_claim`. Resource: `book://{id}/toc`. Long ingestion as a background task.
- Must work from Claude Desktop/Claude Code as well as from the Deep Agent. This is the demo.

### 6. Evals + observability
- Per-book question set with gold locators. Metrics: locator hit rate, citation accuracy and effective citations (FACT-style, per DeepResearch Bench), report quality via a rubric judge (RACE-style).
- Score planning, retrieval, sourcing, claims, synthesis separately (aggregate scores hide which stage failed).
- Trace with Langfuse/OTel (existing stack; a posting lists Langfuse + Grafana).

---

## 4. Portfolio and risk rules

- The $0 edge is a demo risk if the demo needs paid keys. Host a demo on own infra with **public-domain books** (Project Gutenberg); give users a BYOK settings page.
- Rotator is private (HARD RULE in memory): public README/UI says only "any OpenAI-compatible endpoint". This doc is internal.
- User-supplied books are copyrighted: process per-user, do not redistribute derived output, do not ship copyrighted sample books.
- Ship order if time-boxed: Ingest → Book MCP + `book-reader` → Distill → web-researcher + verifier → evals → UI (apps/web) → optional graph.

---

## 5. Open questions (decide before building)

1. Which FastMCP major does Nexus run, and is a 3.x → 4.x move in scope first?
2. Parser bake-off result on 3 real books (Docling vs MinerU vs Marker).
3. Does the Book server live under `domains/` as a new domain (per `docs/CODE-CONVENTIONS.md` and the infra-boundary rule: one `infra/<service>` module per service) — confirm layout before scaffolding.

---

## 6. Caveats

- Hiring evidence: ~19 undated aggregator listings, no salaries, no official G42/MBZUAI/TII data. Requirements seen: LangChain/LangGraph 3-5 yrs in production, multi-agent orchestration, RAG with citations and retrieval evals, reranking, Azure, Langfuse. The real bottlenecks are likely years of production experience and visa/relocation, not the feature.
- Source conflicts: LangChain changelog page showed deepagents v0.7.0 (2026-07-24) vs GitHub 0.7.32 (2026-10-23): GitHub treated as current, the page as stale. A June 2026 review says `open_deep_research` was not archived; the GitHub page (archived 2026-08-21) is newer.
- No book-length benchmark exists; GraphRAG/RAPTOR/agentic results come from multi-hop QA. Test on own corpus.
- DeepResearch Bench leaderboard not retrieved (live on Hugging Face). Judge changed to GPT-5.5 on 2026-05-11; the "Onyx ranks #1" claim is unverified. No official LangChain statement names deepagents as the `open_deep_research` successor (only the OPEA migration PR).
- Pre-existing closest clones were not stress-tested; star counts come from aggregator mirrors.

---

## 7. Evidence

| Claim | Source | Date |
|---|---|---|
| deepagents 0.7.30-0.7.32, async subagents | https://github.com/langchain-ai/deepagents/releases | 2026-10-22/23 |
| `open_deep_research` archived | https://github.com/langchain-ai/open_deep_research | 2026-08-21 |
| OPEA moved from open_deep_research to deepagents | https://github.com/opea-project/GenAIExamples/pull/2334 | 2026 (secondary) |
| FastMCP 4.1.4, breaking notes | https://github.com/PrefectHQ/fastmcp/releases | 2026-10-26 |
| langchain-core 1.7.0, `langchain.mcp`, HITL per-call | https://docs.langchain.com/oss/python/releases/changelog | 2026-10-22 / 09-01 / 10-14 |
| Deep Agents features (subagents, async, skills, MCP, HITL, backends) | https://docs.langchain.com/oss/python/deepagents/overview | 2026-10 |
| Managed Deep Agents public beta | https://www.langchain.com/blog/managed-deep-agents-is-now-in-public-beta | 2026-08-07 |
| DeepResearch Bench, RACE/FACT, judge change | https://github.com/Ayanami0730/deep_research_bench | 2026-09-22 |
| GraphRAG vs agentic dense RAG | https://arxiv.org/pdf/2604.09666 | 2026-04 |
| TOC-based book hierarchy parsing | https://arxiv.org/html/2509.00909 | v2 2026-06 |
| Context-aware hierarchical merging | https://arxiv.org/abs/2502.00977 | 2025 |
| Citation-vs-retrieval-log check, per-stage evals, report-stage degradation | https://futureagi.com/blog/evaluating-deep-research-agents-2026/ | 2026 (vendor) |
| Abu Dhabi senior AI/agentic postings | https://www.jobleads.com/ae/job/senior-ai-engineer--abu-dhabi--edb8cc8623ea8c89e61238b4c71961dff | undated |
| G42 recruiting AI agents | https://www.semafor.com/article/02/27/2026/g42s-bot-recruiting-is-step-toward-the-uaes-ai-first-future | 2026-02-27 |
| Existing book-summarizer clones | https://pypi.org/project/condensr/ , https://www.sourcepulse.org/projects/13956157 | 2026 (aggregator) |
