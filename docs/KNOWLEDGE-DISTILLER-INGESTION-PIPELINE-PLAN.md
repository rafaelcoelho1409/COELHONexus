# Knowledge Distiller — Ingestion Pipeline Build Plan

**Status**: Plan approved, implementation pending
**Date**: 2026-04-21
**Context**: Resolver is production-ready (`services/search_chain.py` + `services/knowledge/docs_resolver.py`). Ingestion layer is Tier-4-only Playwright and not connected to the resolver's tier output. This doc is the self-contained plan so future sessions can pick up without re-reading the whole conversation.

## The problem in one sentence

The resolver outputs `ResolvedDocs(tier=1..4, docs_url, github_discover=readme_only|..., ...)` but `services/knowledge/ingestion.py` is a 1068-LoC Playwright monolith that ignores `tier` and assumes Crawl4AI for every framework — so a Tier-1 `llms-full.txt` site takes 20 min of Playwright instead of 3 seconds of httpx, and a GitHub-only repo takes 20 min of Playwright rendering a file-tree page instead of 5 sec of raw-markdown fetching.

## Current state (April 2026)

- ✅ **Resolver complete** — Exa → Tavily → Jina fallback chain, 4-stage pipeline (Registry → Search → LLM rerank → Validator with D0 root liveness + D2 spot-check), Redis-cached, 60s NIM timeouts.
- ✅ **Resolver tested** on 13 topics (FastAPI, LangChain, py-spy, DeepAgents, NVIDIA GPU Operator, LGTM crossover, LangChain crossover, NVIDIA 5-way crossover) — 100% success, 18 search API calls consumed (~1.8% of monthly Exa free tier).
- ✅ **GitHub discovery** implemented in resolver (`_upgrade_git_host_url()` in `services/knowledge/docs_probe.py`) — `homepage` > `has_pages` > `readme_only`.
- ❌ **Ingestion layer ignores resolver output** — `services/knowledge/ingestion.py::ingest_framework_docs()` takes a plain `docs_url`, does Playwright for everything. 1068 LoC, one function.
- ❌ **`POST /api/v1/knowledge/studies`** doesn't accept `ResolvedDocs` — user must pass a bare `docs_url` string.
- ❌ **Celery task** `tasks.knowledge.distiller.run_knowledge_distiller` doesn't pass `tier` / `github_discover` / `github_org` / `github_repo` / `github_default_branch` through.

## Stability fixes already in Tier 4 (per `docs/CRAWL4AI-POST-RUN-FIXES.md`)

All applied to `services/knowledge/ingestion.py` — ~97% success rate (from 55.7%):
- Per-URL `session_id = crawl-{uuid[:12]}` — isolates BrowserContext
- `max_session_permit = 4` — below Playwright race threshold
- `on_page_context_created` hook — blocks Next.js `/_next/data/**` prefetch + heavy assets
- `wait_until="domcontentloaded"` + JS `readyState=complete` predicate
- `PruningContentFilter(threshold=0.45, threshold_type="dynamic")` via `DefaultMarkdownGenerator`
- `LXMLWebScrapingStrategy` for faster HTML parse

## Tier semantics (from the resolver, already production-ready)

| Tier | Resolver condition | Ingestion strategy | Latency target |
|---|---|---|---|
| **1** | `/llms-full.txt` content-VALID at host root | httpx GET → save single file | ~3s |
| **2** | `/llms.txt` content-VALID | Parse llms.txt → parallel fetch `.md` links via httpx | ~1 min |
| **3** | `/sitemap.xml` content-VALID | Parse `<loc>` entries → filter docs URLs → parallel httpx fetch → trafilatura extract | ~2-5 min |
| **4** | All three MISSING | Crawl4AI + Playwright (existing code) | ~20 min |
| **GH** | `source_signals.github_discover == "readme_only"` | GitHub API `/git/trees?recursive=1` → filter `*.md` → parallel `raw.githubusercontent.com` fetch | ~5s |

Tier-GH is a sub-case of Tier 4 (`tier=4 + readme_only`). Dispatcher checks github_discover first.

## Architecture (2026 best practice, per `docs/CRAWLER-HYBRID-RS-TRAFILATURA.md` + deep research 2026-04-21)

**Long-term**: LangGraph `add_conditional_edges` + `Send()` for per-URL fan-out + `astream(stream_mode="custom")` SSE.

**Short-term (steps 1-6 below)**: plain async if/elif dispatcher inside `ingest_framework_docs()`. Simpler, unblocks pipeline faster. Migrate to LangGraph in step 7 when we need streaming.

## Library choices (April 2026)

| Purpose | Library | Version |
|---|---|---|
| HTTP client (everywhere) | `httpx` | 0.28.x (already in stack) |
| Async rate-limiter | `aiolimiter` | 1.2 |
| Async retries | `tenacity` | 9.x |
| llms.txt parser | `llms-txt` | 0.0.6 (AnswerDotAI, PyPI) |
| HTML→markdown (Tier 3) | `trafilatura` | ≥2.0 — pure Python, F1 ≈ 0.791 on docs. rs-trafilatura (F1 0.931) considered but rejected — ships no cp313 wheels, needs Rust toolchain in image. |
| Tier 4 browser crawler | `crawl4ai` | 0.8.0 (already in stack) |
| LangGraph (step 7) | `langgraph` | 1.1.x (already in stack) |

## Build order — 9 steps

Each step is a self-contained session with its own verification. The first 2 ship together as the minimum-viable unlock; the rest are prioritized by value-per-hour.

### Step 1 — Resolver → Ingestion wiring  (~250 LoC, 2-3h) — ESSENTIAL

**Why first**: without this, resolver output goes nowhere. The whole pipeline stays dead.

1. Extend `schemas/knowledge/inputs.py::CreateStudyRequest` to optionally accept a `ResolvedDocs` payload (or a `resolved_doc_id` that recovers from Redis cache).
2. In `routers/v1/knowledge/distiller.py::create_study`, when `ResolvedDocs` is present, extract `tier`, `docs_url`, `repo_url`, `source_signals.github_discover`, `source_signals.org`, `source_signals.repo`, `source_signals.github_default_branch`. Pass all to the Celery task.
3. In `tasks/knowledge/distiller.py::run_knowledge_distiller`, extend signature to accept `tier`, `github_discover`, `github_org`, `github_repo`, `github_default_branch`. Plumb through to `ingest_framework_docs`.
4. In `schemas/knowledge/ingestion.py::DocsIngestionConfig`, add the same fields.
5. In `services/knowledge/ingestion.py`, add an if/elif dispatcher at the top of `ingest_framework_docs()`:
   ```python
   async def ingest_framework_docs(cfg, storage, cache=None):
       # Tier-GH short-circuit (github readme-only repos)
       if cfg.github_discover == "readme_only":
           return await _ingest_github_tree(cfg, storage)
       # Tier 1-3 branches to be added in later steps
       # Default: full Crawl4AI path (existing 1068-LoC implementation)
       return await _ingest_crawl4ai(cfg, storage, cache)
   ```
6. Rename current monolith body → `_ingest_crawl4ai()`. No logic change, just relocation.

**Verification**: re-run `POST /studies/resolve` + `POST /studies` on FastAPI. Check Celery logs show tier passed through. Ingestion still works for Tier 4 (SearXNG → Playwright → MinIO).

### Step 2 — Tier-GH  (`_ingest_github_tree()`, ~150 LoC, 2h) — HIGH VALUE

**Why second**: rescues py-spy / NVIDIA DCGM / CLIP / Whisper / any README-only GitHub repo from wasted Playwright runs. ~19.5 min saved per GitHub-only framework.

1. Create `services/knowledge/github_ingest.py` with:
   ```python
   async def _ingest_github_tree(cfg: DocsIngestionConfig, storage: MinIOStudyStorage) -> IngestResult:
       # 1. GET api.github.com/repos/{org}/{repo}/git/trees/{default_branch}?recursive=1
       # 2. Filter tree for blob nodes with paths ending in .md or .mdx
       # 3. Exclude paths starting with: node_modules/, .github/, vendor/, tests/, test/
       # 4. Parallel GET raw.githubusercontent.com/{org}/{repo}/{branch}/{path}
       #    - aiolimiter (rate=10/sec per host)
       #    - tenacity retry on 429/5xx (max 3, exponential jitter)
       # 5. For each fetched markdown: use existing _write_raw() to save to MinIO
       # 6. Return IngestResult with manifest
   ```
2. Use `GITHUB_TOKEN` env var (already in project secrets) — bumps API limit from 60/hr to 5000/hr.
3. Uniform MinIO output layout (matches Tier 4):
   ```
   {study_root}/research/raw/
     manifest.json       # {tier_used: "github_readme_only", framework, n_success, failures}
     {slug}.md           # body
     {slug}.meta.json    # {url, title, tier_source, content_hash, fetched_at}
   ```
4. Register the function in `ingestion.py`'s dispatcher (step 1 already wired).

**Verification**: `POST /studies` with a py-spy resolver result. Expect ~5 sec total, 1 file saved (`README.md` → something like `readme.md`). Check MinIO via `/studies/{id}/tree`.

### Step 3 — Tier 1 (`_ingest_llms_full_txt()`, ~50 LoC, 1h)

**Why third**: trivial implementation, huge speedup for LangChain-class sites (DeepAgents, LangChain, any MkDocs-material site with llms-full.txt enabled).

1. Create `_ingest_llms_full_txt(cfg, storage)`:
   ```python
   # 1. GET {host_root}/llms-full.txt (already known VALID from resolver D-probe)
   # 2. Use existing _write_raw() — single file output
   # 3. Manifest: tier_used="llms_full_txt", n_success=1
   ```
2. Add branch in ingestion dispatcher: `if cfg.tier == 1: return await _ingest_llms_full_txt(...)`.

**Verification**: `POST /studies` with LangChain or DeepAgents ResolvedDocs → expect ~3 sec, 1 file output.

### Step 4 — Tier 3 (sitemap httpx fast path, ~200 LoC, 3h)

**Why fourth**: Tier 3 is the most common classification (~50% of frameworks — FastAPI, Grafana, most docs sites). Cutting 20 min → 2-5 min is a huge UX win.

1. Install `trafilatura` via `uv pip install trafilatura` (pure Python,
   no Rust toolchain). rs-trafilatura was considered (higher F1) but
   dropped — see Step 5 note.
2. Create `_ingest_sitemap_httpx(cfg, storage)`:
   ```python
   # 1. GET {host_root}/sitemap.xml (already VALID per resolver)
   # 2. Recursively unwrap sitemap indexes (<sitemapindex> → follow sub-sitemaps)
   # 3. Filter <loc> URLs: must be under docs_url path, exclude /blog/, /news/, /release-notes/
   # 4. Parallel GET with Semaphore(10) + tenacity retries
   # 5. For each HTML: trafilatura.extract(html, output_format="markdown")
   # 6. Apply existing _passes_content_quality() gate
   # 7. _write_raw() to MinIO
   ```
3. Reuse existing language-scoping filters (`_build_language_filter`, `_is_polyglot_framework`) from `ingestion.py`.
4. Dispatcher: `if cfg.tier == 3: return await _ingest_sitemap_httpx(...)`.

**Verification**: `POST /studies` with FastAPI → expect 2-5 min, ~50-150 pages. Compare quality vs Tier 4 Playwright output of the same framework.

### Step 5 — DROPPED (rs-trafilatura swap)

Originally planned as a Tier 3/4 extractor upgrade (pure-Python trafilatura
F1 ≈ 0.791 → Rust-PyO3 rs-trafilatura F1 ≈ 0.931). **Dropped** because
rs-trafilatura 0.1.1 only ships `cp312` wheels and our image runs Python
3.13; source-compile needs `rustc + gcc` added to the Dockerfile. Upstream
is slow to add cp313 wheels, and we've decided the F1 delta isn't worth
adding a Rust toolchain to the image. Trafilatura (pure Python) is the
permanent Tier 3/4 extractor.

If upstream ships cp313 wheels in the future, revisit as a one-line swap
inside `_extract_markdown` in sitemap_ingest.py / llms_txt_ingest.py.

### Step 6 — Tier 2 (`_ingest_llms_txt()`, ~100 LoC, 1-2h)

**Why sixth**: llms.txt adoption is still 0.011% of sites (LinkedIn May 2025 stat). Low-frequency but non-zero — NVIDIA deeplearning docs have it. Build after higher-volume tiers.

1. `uv pip install llms-txt` (version 0.0.6).
2. Create `_ingest_llms_txt(cfg, storage)`:
   ```python
   from llms_txt import parse   # official AnswerDotAI parser
   # 1. GET {host_root}/llms.txt
   # 2. parse() yields structured sections (H2 + [title](url): desc bullets)
   # 3. Extract .md URLs from the bullets
   # 4. Parallel fetch each (rate-limited) + save via _write_raw()
   ```
3. Dispatcher: `if cfg.tier == 2: return await _ingest_llms_txt(...)`.

**Verification**: `POST /studies` with NVIDIA GPU Operator (resolver returns Tier 2) → expect ~1 min, moderate page count.

### Step 7 — LangGraph dispatcher refactor  (~150 LoC, 2h)

**Why seventh**: clean architecture + prerequisite for SSE streaming (step 8). Defer until tiers 1-6 are working.

1. Create `graphs/knowledge/ingestion.py`:
   ```python
   builder = StateGraph(IngestState)
   builder.add_node("classify", _classify_node)   # reads resolved.tier
   builder.add_node("tier1", _tier1_node)
   builder.add_node("tier2_plan", _tier2_plan_node)  # returns list[Send("fetch_one", ...)]
   builder.add_node("tier3_plan", _tier3_plan_node)
   builder.add_node("tier4", _tier4_node)
   builder.add_node("tier_gh", _tier_gh_node)
   builder.add_node("fetch_one", _fetch_one_node)
   builder.add_conditional_edges("classify", _route_by_tier)
   ```
2. Swap `ingest_framework_docs()` body to invoke the graph.
3. Celery task calls `graph.ainvoke(initial_state)`.

**Verification**: All previous tier tests still pass; graph visualization via `graph.get_graph().draw_mermaid_png()`.

### Step 8 — SSE streaming `/studies/{id}/ingest/stream`  (~100 LoC, 2h)

**Why eighth**: real-time progress for 20-min Tier 4 runs. UX unlock.

1. In `graphs/knowledge/ingestion.py`, per-fetch node emits via `get_stream_writer()`:
   ```python
   writer = get_stream_writer()
   writer({"event": "page", "url": url, "n": i, "total": N, "status": "ok"})
   ```
2. New router endpoint in `routers/v1/knowledge/distiller.py`:
   ```python
   @router.get("/studies/{study_id}/ingest/stream")
   async def stream_ingest(study_id, request):
       graph = build_ingestion_graph(...)
       async def events():
           async for chunk in graph.astream(state, stream_mode="custom"):
               yield f"data: {json.dumps(chunk)}\n\n"
       return StreamingResponse(events(), media_type="text/event-stream")
   ```

**Verification**: `curl -N /studies/{id}/ingest/stream` — should print `{"url":..., "n":1, "total":N}` per page.

### Step 9 — Router split (per `docs/KNOWLEDGE-DISTILLER-ROUTER-SPLIT.md`)  (~200 LoC, 2h)

**Why last**: code hygiene, not user-facing. Fine to skip if the 9-step plan runs out of runway.

1. Split `routers/v1/knowledge/distiller.py` → `content.py` + `agents.py` + `helpers.py`.

## Cumulative effort

| Milestone | Steps | LoC | Hours | Outcome |
|---|---|---|---|---|
| MVP (essential + GitHub) | 1+2 | ~400 | 4-6 | End-to-end pipeline works; GitHub-only frameworks fast |
| Fast-path wave | 1+2+3+4 | ~650 | 8-10 | ~50% of frameworks under 5 min instead of 20 |
| Quality wave | + 5 | ~680 | 9-11 | +4.3 F1 across Tier 3/4 |
| Full wave | + 6 | ~780 | 10-13 | All tiers implemented |
| Observable | + 7+8 | ~1030 | 14-17 | LangGraph + SSE progress streaming |
| Clean | + 9 | ~1230 | 16-19 | Code hygiene complete |

## Related docs

- `docs/KNOWLEDGE-DISTILLER-RESOLVER-STRATEGY.md` — resolver design (complete)
- `docs/CRAWL4AI-POST-RUN-FIXES.md` — Tier 4 stability patches already applied
- `docs/CRAWLER-HYBRID-RS-TRAFILATURA.md` — rs-trafilatura hybrid strategy
- `docs/KNOWLEDGE-DISTILLER-ARCHITECTURE.md` — overall KD architecture
- `docs/KNOWLEDGE-DISTILLER-ROUTER-SPLIT.md` — step 9 details

## How to resume

Point a new Claude Code session at this doc and say: "Continue the ingestion pipeline build plan from step N". Each step is self-contained with its own verification criteria — no conversation history needed.
