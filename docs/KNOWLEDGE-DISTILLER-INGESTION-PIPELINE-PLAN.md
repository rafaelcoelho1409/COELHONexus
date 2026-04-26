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

---

# V2 Expansion — Crawler-light source ladder (deferred until KD synth quality stabilizes)

**Status: design-complete, NOT YET IMPLEMENTED.** Captured here from the
2026-04-26 deep-research pass so we don't lose the validated facts. To be
built only AFTER OP-HIERARCHICAL-SYNTH and the rest of the synth-quality
backlog are settled and Run-N is consistently producing ACCEPT-grade
chapters. The current ingest tiers (1, 2, 3, 4) are good enough that
ingestion is not the bottleneck — synth is — so source-ladder expansion
is correctly behind the synth work.

## Why this expansion

Current ladder ends at Crawl4AI/Playwright (Tier 4). Empirical Run-21
evidence (Pydantic, 2026-04-26) shows Tier 4 actually works well for
mainstream documentation sites (369/373 pages succeeded, 4 transient
failures all retried successfully). However:

  - JS-heavy enterprise docs (NVIDIA TensorRT, docs.microsoft.com
    Q#, vendor portals with WAF challenges) will reliably fail.
  - Books are not covered by ANY tier currently.
  - Crawl4AI is the slowest tier per-page (~13 pages/min for Pydantic vs
    near-instant for llms-full.txt) and the most fragile.
  - We pay browser-render overhead even when an author-curated or
    GitHub-hosted source exists.

The 2026-04-26 research validated 8+ external sources and 3 self-hostable
options that solve different slices of these problems. The strategic
opportunity: route to structured/API sources FIRST and keep crawlers as
genuine last resort.

## Validated source catalog (2026-04-26 research)

| # | Source | What it returns | API / pricing | Freshness | Coverage breadth | Best slot in ladder | Gotchas |
|---|---|---|---|---|---|---|---|
| 0 | **DevDocs.io** (freeCodeCamp, OSS) — `devdocs.io` | Bulk-downloadable tarballs at `downloads.devdocs.io/{slug}.tar.gz` containing raw HTML pages of official docs + structured `index.json` (table of contents) + `db.json` (path → HTML body lookup) + `meta.json` (version + source URL); manifest at `devdocs.io/docs.json` (1.3 MB JSON listing all slugs) | **Free, no auth, no rate limit**; self-hostable | Verified actively maintained 2026-04-22 (Pandas 3.0.8, Git 2.54.0, TypeScript 6.0.3 all updated that day); refresh cadence per slug visible in `mtime` field of manifest | **794 docsets across 119 doc families** including FastAPI, Django, Flask, NumPy, SciPy, Pandas, DuckDB, TypeScript, Click, Bottle, Falcon, Sanic, Python 3.8-3.13 stdlib (incl. asyncio), Git, Docker, MDN, etc. **Sparse for AI/ML ecosystem**: NO Pydantic, LangChain, vLLM, Qdrant, TensorRT-LLM, NetworkX, Sweetviz | **Tier 0b (highest-value addition for mainstream/web/Python)** — pre-cache all 794 slugs once into MinIO (~few GB total), refresh weekly via `mtime` diff in manifest. Zero infra cost forever after initial download | HTML is DevDocs-cleaned (CSS/nav stripped, code blocks preserved) — not pristine upstream HTML; covers maybe 30% of a typical AI/ML roadmap by name (but ~80% of the classical web/Python stack) |
| 1 | **Context7** (Upstash) — `context7.com` | Curated, version-pinned code snippets + condensed Markdown per library; MCP and REST | MCP at `mcp.context7.com`; **Free 1,000 req/mo (~33/day)**, Pro $10/seat for 5k req, $10/1k overage | Re-indexed continuously; spot-check showed vLLM "2 days ago", TensorRT-LLM "1 week ago" | **~33,000+ libraries** indexed (largest of the dedicated docs-MCP services in 2026) | **Tier 1b default** for any popular OSS lib | Free quota is small for production scale; trust scores vary; long-tail libs (e.g. Sweetviz) absent |
| 2 | **DeepWiki** (Cognition) — `deepwiki.com` | AI-generated multi-page wikis per GitHub repo (overview / arch / APIs / glossary); free Q&A | Public site free; programmatic via the **DeepWiki MCP** (`mcp.deepwiki.com`); paid tier inside Devin app | Re-index on commit; spot-check: vLLM commit `e9f331` indexed 2026-04-24, TensorRT-LLM 2026-04-22 | Any public GitHub repo (auto-indexed on demand). Long tail = "Loading…" until first request | **Tier 2 for repos with thin docs** (architecture narration is unique value) | No published rate limits; AI-generated content can hallucinate → MUST run through Tier 0a vault audit; for niche repos may be empty until triggered |
| 3 | **GitMCP** — `gitmcp.io/{owner}/{repo}` | MCP-only proxy that exposes a repo's `llms.txt` / `llms-full.txt` / `README.md` / docs over MCP | Free, no auth required (static URL pattern) | As fresh as the upstream repo (no separate index) | Any GitHub repo (universal — confirmed for both Qdrant and Sweetviz) | **Tier 2b fallback** when Context7/DeepWiki miss; cheapest universal route | Quality entirely depends on whether the repo bothers to ship docs; pure pass-through, not curation |
| 4 | **`llms.txt` / `llms-full.txt` direct fetch** | Raw Markdown index (`llms.txt`) or full bundle (`llms-full.txt`) hosted by the project | Plain HTTPS GET, free | Whatever the project re-publishes on doc rebuild | ~400-500+ projects in [llmstxt.directory](https://directory.llmstxt.cloud); ~9-13k in [llms-txt-hub](https://github.com/thedaviddias/llms-txt-hub). Confirmed: Qdrant ✓, Pydantic-AI ✓, Pydantic core ✗, vLLM ✗, TensorRT-LLM ✗ | **Tier 0a / Tier 1 (current)** — already implemented | Coverage is patchy; [Pydantic still has no llms-full.txt](https://github.com/pydantic/pydantic-ai/issues/1028); discovery requires probing |
| 5 | **ReadTheDocs / Sphinx `_sources/*.rst.txt`** | Raw RST source — confirmed at `https://docs.python.org/3/_sources/library/asyncio.rst.txt` | Plain HTTPS GET, free | As fresh as the doc build | Any Sphinx-built site that ships sources (default-on for RTD; CPython, NumPy, SciPy, Matplotlib, Sphinx itself) | **Tier 1c** for scientific Python and CPython; clean structured RST beats AI distillation | Some sites disable `_sources/` (e.g., NVIDIA TensorRT-LLM didn't reference it on the index page); MkDocs sites don't expose this at all |
| 6 | **Tavily** — `docs.tavily.com` | Search / Extract / Crawl / Map / Research, all returning Markdown | **Free 1,000 credits/mo, no card**; pay-as-you-go $0.008/credit; Search=1cr basic / 2cr advanced; Extract = 5 URLs per credit basic | Real-time | Whole web | **Tier 3b crawler replacement** for arbitrary doc URLs that have no llms.txt and no GitHub repo | Counts toward web; not a curated docs index — you still need URL discovery |
| 7 | **Exa** — `exa.ai` | Search + dedicated *code-docs* index, Contents endpoint with 90% token reduction, Highlights | **Free 1,000 req/mo**; Search $7/1k, Contents $1/1k, Deep Search $12/1k, Answer $5/1k; $1k startup grant | Real-time | Whole web; *dedicated code-docs index* makes it stronger than Tavily for our use-case | **Tier 3b alternative to Tavily** when you need semantic ("find similar") retrieval | More expensive per search than Tavily; no built-in MCP for libraries |
| 8 | **Linkup** — `linkup.so` | Standard + Deep search; JSON `sourcedAnswer` (snippets, not full Markdown) | Free tier exists, pay-as-you-go, enterprise custom | Real-time | Whole web; #1 SimpleQA factuality | **Tier 4b** — only if Tavily/Exa fail to factually ground a query | Output is snippets, not raw Markdown — extra extract step needed; less suited to bulk doc ingestion |
| 9 | **Mintlify hosted docs** | `llms.txt` exposed per site; **no public JSON/Markdown API** | Free to fetch | Per doc-site rebuild | All Mintlify-hosted projects (Anthropic, Cursor, etc.) | **Tier 0a** for any Mintlify-hosted project — fetch the site's own `/llms.txt` (already covered by current Tier 1 path) | No structured client API — you treat it the same as #4 |
| 10 | **PyPI / npm / pkg.go.dev / crates.io JSON** | Version-pinned README + metadata | `https://pypi.org/pypi/{pkg}/json` etc. — free, ETag-cached at CDN | Per release | Universal for each ecosystem | **Tier 1d for canonical version-pinned README** | README only — not full docs; format is whatever the maintainer wrote |
| 11 | **HuggingFace Hub** | Model/dataset cards as raw Markdown via Hub API; full OpenAPI at `https://huggingface.co/.well-known/openapi.json` | Free, optional auth for higher limits | Live | All HF models & datasets | **Tier 1e for ML model/dataset cards** specifically | Card-only, not framework-wide docs |
| 12 | **Anthropic Citations API** | *Not* a doc source — it's an inference-time feature that takes documents you already supply and returns grounded citations | Standard Anthropic API pricing; cited_text doesn't count toward output tokens | N/A | N/A | **Apply at synthesizer stage**, not ingestion. Use it on the distilled bundle so the synthesizer's claims are pinned to source spans | Doesn't fetch anything — orthogonal to the ingestion ladder; tracked separately as Tier 3 #22 in IMPROVEMENTS-ROADMAP |
| 13 | **Sourcegraph public search** | Web UI search across 2M+ public repos | **Free self-hosted/Pro killed July 2025**; only the public web search at `sourcegraph.com/search` remains free; API access now enterprise-only ($49+/user/mo) | Live | 2M+ OSS repos | **SKIP** — pivoted to Cody, deprioritized code search; not viable as an API source in 2026 | The free API is gone |
| 14 | **Greptile** | Repo-graph code search + Q&A | **No real free tier**; 150 free units, then $0.45/req Genius API or $30/seat Cloud; 50% off pre-Series-A startups, free for qualifying OSS | Per-commit re-index | Any repo you point it at | **SKIP for cost reasons** unless we qualify for the OSS/startup discount | $0.45 per request makes bulk ingestion infeasible self-funded |

**Newer entrants worth tracking** (2025-2026 launches in the same space): [docs-mcp-server](https://github.com/arabold/docs-mcp-server) (open-source self-hosted that ingests websites + GitHub + npm + PyPI + local files into your own indexed MCP server — explicitly markets itself as "alternative to Context7, Nia, Ref.Tools"), [Docfork](https://docfork.com/) (~9-10k libs, smaller than Context7), [Ref.tools](https://docs.ref.tools) (paid public+private docs MCP), [Nia](https://nia.ai).

## Coverage matrix (5 representative libraries × 7 candidate sources)

Validated 2026-04-26 against live endpoints. Legend: **Y** present / **P** partial / **N** absent.

| Library | Context7 | DeepWiki | GitMCP | llms.txt direct | Sphinx `_sources/` | Tavily/Exa (web) | PyPI/HF JSON |
|---|---|---|---|---|---|---|---|
| **vLLM** (vllm-project/vllm) | **Y** — ~10,056 snippets, indexed 2 days ago | **Y** — ~13 sections / 40+ pages, indexed 2026-04-24 | **Y** — `gitmcp.io/vllm-project/vllm` | **N** — `docs.vllm.ai/llms.txt` returns 404 | **P** — docs.vllm.ai is MkDocs-Material; no `_sources/` | **Y** — well-indexed on the open web | **Y** — PyPI `vllm` JSON has README |
| **Pydantic** (pydantic/pydantic) | **Y** — Context7 ranks Pydantic in its top libraries | **Y** — repo is auto-indexable | **Y** — `gitmcp.io/pydantic/pydantic` | **P** — *Pydantic-AI* publishes `ai.pydantic.dev/llms-full.txt`; **core Pydantic still has none** ([open issue #1028](https://github.com/pydantic/pydantic-ai/issues/1028)) | **N** — docs.pydantic.dev is MkDocs | **Y** | **Y** — PyPI |
| **NVIDIA TensorRT-LLM** | **Y** — 4,180 snippets, indexed 1 week ago | **Y** — 23 major sections, indexed 2026-04-22 | **Y** — `gitmcp.io/NVIDIA/TensorRT-LLM` | **N** — no `llms.txt` at the docs root | **P** — site is Sphinx but `_sources/` not referenced on landing page; needs probing per-page | **Y** | **N** — not on PyPI as a normal pip package; releases on GitHub Releases page |
| **Qdrant** (qdrant/qdrant) | **Y** — Context7 covers Qdrant SDK + server | **Y** — repo indexable | **Y** — confirmed `gitmcp.io/qdrant/qdrant` | **Y** — [`qdrant.tech/llms-full.txt`](https://qdrant.tech/llms-full.txt) confirmed (~200 KB, mixed timestamps through 2024) | **N** — Hugo site, not Sphinx | **Y** | **Y** — `qdrant-client` on PyPI |
| **Sweetviz** (fbdesignerfr/sweetviz) | **N** — no Context7 page resolved at `/fbdesignerfr/sweetviz`; long-tail miss | **P** — DeepWiki page exists but stays in "Loading…" until someone triggers indexing | **Y** — `gitmcp.io/fbdesignerfr/sweetviz` works (proxies README) | **N** | **N** | **P** — sparse, mostly the README repeated | **Y** — PyPI `sweetviz` JSON has README |

**Reading the matrix:** the only library where multiple top sources whiff is Sweetviz (the deliberate long-tail probe). For everything else, Context7 + DeepWiki + GitMCP combined provide multi-redundant coverage even when llms-full.txt is missing.

## Proposed expanded ladder

Renumbered to make room for new tiers without breaking the existing
mental model. Old tier numbers preserved as parentheticals.

```
Tier 0    PDF / EPUB upload                  ← books (~15 items in current roadmap)
Tier 0a   Direct llms-full.txt + llms.txt    (current Tier 1 + Mintlify-hosted)
Tier 0b   DevDocs.io tarball cache           (NEW — pre-fetch all 794 slugs to MinIO,
                                              refresh weekly; instant zero-cost for
                                              mainstream web/Python ecosystem)
Tier 1a   Context7 MCP                       (NEW — ~33k popular libs)
Tier 1b   Sphinx _sources/*.rst.txt probe    (NEW — Python stdlib + scientific Python)
Tier 1c   PyPI / npm / HF Hub README JSON    (NEW — version-pinned baseline)
Tier 1d   GitHub raw /docs/**/*.md           (NEW — long-tail OSS that ships docs in-repo)
Tier 2    llms.txt + sitemap                 (current Tier 2)
Tier 2a   DeepWiki MCP                       (NEW — repos with thin official docs)
Tier 2b   GitMCP universal pass-through      (NEW — free fallback for ANY GitHub repo)
Tier 2c   Read the Docs htmlzip               (NEW — zip download of full HTML for any
                                              RTD-hosted Sphinx project: vLLM, NetworkX,
                                              etc.; URL pattern
                                              <slug>.readthedocs.io/_/downloads/en/latest/htmlzip/)
Tier 3    Sitemap + httpx + trafilatura      (current Tier 3)
Tier 3a   Tavily Extract / Exa Contents      (NEW — arbitrary URLs without crawler)
Tier 4    Crawl4AI Playwright                (current Tier 4 — TRULY last resort)
```

Synth-stage add-on (orthogonal to ingestion):
- **Anthropic Citations API** wrapping the distilled bundle so synthesizer claims pin back to source spans without burning output tokens. Already on the IMPROVEMENTS-ROADMAP as Tier 3 #22.

## Strategic add-on: self-hosted `docs-mcp-server`

[arabold/docs-mcp-server](https://github.com/arabold/docs-mcp-server) is
the highest-leverage architectural move surfaced by the research. It's
an open-source MCP server that ingests websites + GitHub + npm + PyPI
+ local files into your own searchable index. Markets itself
explicitly as an "alternative to Context7, Nia, Ref.Tools."

Why this is strategic for COELHONexus:
  - **Eliminates per-request cost** at every paid tier (Context7, Tavily, Exa)
    because we ingest each (lib, version) once into our own index.
  - **Solves the "library not indexed" problem** — if Context7 doesn't
    have something, we just point docs-mcp-server at the source URL and
    it indexes for us.
  - **Caches forever** — pair with our existing `_cache/ingestion/`
    pattern to make re-runs free.
  - **Composable** — sits BEHIND the tier ladder above. Tier 1a-1d, 2a-2b,
    3a all become "check docs-mcp-server first; if miss, hit upstream
    and feed result back into docs-mcp-server."

This is the eventual end-state: docs-mcp-server as our cached ingestion
backend, with the upstream sources (Context7, DeepWiki, GitMCP, llms.txt,
PyPI, etc.) only consulted on cache miss.

## Implementation effort estimate

Per new tier, in priority order. Numbers are rough — assume 2× for
test coverage + verification + roadmap-doc updates.

| New tier | Effort | Dependency | Roadmap coverage delta |
|---|---|---|---|
| **Tier 0b (DevDocs.io bulk cache)** | ~120 LoC: fetch `docs.json` manifest, walk slugs, GET each tarball, untar, store in MinIO under `_cache/devdocs/{slug}/`; weekly cron checks `mtime` per slug | MinIO (already have); ~few-GB storage one-time | **+30% mainstream Python/web stack** instantly + zero-cost forever |
| Tier 1a (Context7 MCP) | ~80 LoC + Context7 API key | none | +30% (popular libs) |
| Tier 2b (GitMCP) | ~50 LoC | none (no auth) | +60% (any GitHub repo) |
| Tier 2c (Read the Docs htmlzip) | ~60 LoC: probe `<slug>.readthedocs.io/_/downloads/en/latest/htmlzip/`, unzip, walk HTML | none | +20% (Sphinx-built RTD-hosted libs: vLLM, NetworkX, NumPy, SciPy, etc.) |
| Tier 1d (raw GitHub /docs/*.md) | ~100 LoC | GitHub token (free) | +40% (long-tail OSS that ships docs in-repo) |
| Tier 1c (PyPI/npm/HF README JSON) | ~60 LoC per ecosystem | none | +20% (version-pinned baselines) |
| Tier 1b (Sphinx _sources probe) | ~40 LoC + objects.inv detection | none | +15% (CPython, NumPy, SciPy, etc.) |
| Tier 2a (DeepWiki MCP) | ~60 LoC | none (free MCP) | +25% (repos with thin docs) |
| Tier 3a (Tavily / Exa Extract) | ~80 LoC + API keys | paid above 1k req/mo | +5% (truly orphan URLs) |
| Tier 0 (PDF/EPUB upload) | ~200 LoC + PyMuPDF + chunking | none | +18% (books, currently 0%) |
| `docs-mcp-server` self-hosted backend | ~150 LoC integration + Helm chart | Docker/k8s | INFRA win — caches everything |

**Estimated total**: ~820 LoC across 9 components, ~80-120 hours of focused
work. Should NOT be started until KD synth is consistently producing
ACCEPT-grade chapters (probably post Run-25+).

## Build order (when ready)

Recommend tackling in this sequence to get coverage win first, infra win second:

1. **Tier 0b DevDocs.io bulk cache** — highest-ROI single move. ~120 LoC + cron job. Pre-fetch all 794 slugs into MinIO once, refresh weekly. Solves the entire mainstream web/Python stack at zero per-request cost forever. Run-21-class crawl-spend on FastAPI, DuckDB, Click, etc. drops to literally zero.
2. **Tier 2b GitMCP** — universal fallback, free, no auth, tiny LoC. Immediate +60% long-tail coverage with one PR.
3. **Tier 1a Context7 MCP** — direct quality win for top-popular libs. Unblocks Pydantic / FastAPI / etc. without crawler.
4. **Tier 2c Read the Docs htmlzip** — instant +20% for Sphinx-built RTD-hosted libs (vLLM, NetworkX, NumPy, SciPy). One probe + unzip = full HTML corpus.
5. **Tier 1c PyPI/npm/HF README JSON** — version-pinned baseline. Cheap and orthogonal.
6. **Tier 0 PDF/EPUB upload** — unlocks the books category in the user's roadmap. Larger effort but high payoff.
7. **Tier 1b Sphinx _sources probe** — niche but pure win for scientific Python.
8. **Tier 1d raw GitHub /docs/*.md** — overlaps with GitMCP but is more controllable.
9. **Tier 2a DeepWiki MCP** — last because AI-generated content needs Tier 0a vault audit to verify code preservation.
10. **Tier 3a Tavily / Exa** — only if Tier 0-2 leave a meaningful gap.
11. **`docs-mcp-server` self-hosted** — refactor BEHIND all of the above to cache forever. Last because it requires the upstream sources to be working first.

Crawl4AI demoted to literal last resort. Realistic post-V2 routing for
the user's ~85-item roadmap: ~98% solved without ever spinning a browser.

## Coverage validation (Run-21 evidence baseline)

Before building any of this, the V2 ladder MUST be benchmarked against
Run-21's Pydantic ingest to confirm the same 369 pages can be reproduced
without Crawl4AI. If Tier 1a Context7 + Tier 2b GitMCP + Tier 1d raw
GitHub `/docs/*.md` together yield ≥ 350 of the 369 pages, V2 is
validated and Crawl4AI can be confidently demoted.

## Source URLs (research provenance, 2026-04-26)

- Context7: https://context7.com — pricing https://context7.com/plans
- DeepWiki: https://deepwiki.com — Devin docs https://docs.devin.ai/work-with-devin/deepwiki
- GitMCP: https://gitmcp.io
- llms.txt directories: https://directory.llmstxt.cloud — https://github.com/thedaviddias/llms-txt-hub
- Sphinx `_sources/`: https://docs.python.org/3/_sources/library/asyncio.rst.txt (verified)
- Tavily: https://docs.tavily.com — credits https://docs.tavily.com/documentation/api-credits
- Exa: https://exa.ai — pricing https://exa.ai/pricing
- Linkup: https://linkup.so
- Mintlify: https://mintlify.com/docs
- Anthropic Citations API: https://platform.claude.com/docs/en/build-with-claude/citations
- PyPI JSON API: https://docs.pypi.org/api/json/
- HuggingFace Hub API: https://huggingface.co/docs/hub/en/api
- docs-mcp-server (open-source self-hosted): https://github.com/arabold/docs-mcp-server
- Docfork: https://docfork.com — Ref.tools: https://docs.ref.tools — Nia: https://nia.ai
- Sourcegraph deprecation context: https://www.morphllm.com/comparisons/sourcegraph-alternative
- Greptile pricing: https://www.greptile.com/pricing
- Top-7 Context7 alternatives 2026: https://dev.to/moshe_io/top-7-mcp-alternatives-for-context7-in-2026-2555
- DevDocs.io: https://devdocs.io — manifest at https://devdocs.io/docs.json (1.3 MB JSON, 794 slugs)
- DevDocs tarball example: https://downloads.devdocs.io/python~3.13.tar.gz (7 MB)
- freeCodeCamp/devdocs source: https://github.com/freeCodeCamp/devdocs (221 scrapers, recent commits 2026-04-22)
- Read the Docs htmlzip pattern: https://<slug>.readthedocs.io/_/downloads/en/latest/htmlzip/
- Dash User Contributions (615 docsets): https://github.com/Kapeli/Dash-User-Contributions
- tldr-pages: https://github.com/tldr-pages/tldr — cheat.sh: https://cht.sh/ — devhints: https://devhints.io/
