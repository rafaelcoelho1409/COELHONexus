# Docs Distiller pipeline — current vs SOTA comparison (2026-05-23)

Side-by-side audit of the **Ingestion + Planner + Synth** pipelines against May 2026 published SOTA. Builds on the existing pipeline-specific SOTA docs ([`PLANNER-ARCHITECTURE-2026-05-17.md`](./PLANNER-ARCHITECTURE-2026-05-17.md), [`SYNTH-ARCHITECTURE-SOTA-2026-05-18.md`](./SYNTH-ARCHITECTURE-SOTA-2026-05-18.md)) — both still mostly correct as architecture, but components lag in specific places.

## TL;DR

| Pipeline | Architectural verdict | Component verdict | Biggest gap |
|---|---|---|---|
| **Ingestion** | Frontier (tier-based with llms.txt priority is the consensus pattern) | One generation behind in 3 layers: stealth, extraction, dedup | No incremental-crawl mechanism (ETag/lastmod) |
| **Planner** | Frontier (LITA hybrid is the right shape) | Lagging in 4 nodes: embedder, clustering, rerank, refine | Reduce stage is flat-clustering (Clio); SOTA is graph-structured (HippoRAG 2 / SurveyG) |
| **Synth** | **At the frontier** as of May 2026 (6-node SDP→digest→SAWC→checklist→MGSR→render) | One paper newer (AutoChecklist Mar 2026); MGSR naming is homegrown | No cross-chapter coherence pass — only within-chapter consistency |

**Honest framing**: this is not a "rewrite needed" situation. The user's three pipelines are within striking distance of published SOTA. The deltas below are surgical upgrades, each gated by its own observable signal once OTel/LangFuse are wired.

---

## 1. INGESTION

### Current state (`apps/fastapi/domains/dd/ingestion/`)

Five-tier fallback chain, all implemented, all live:

```
tier1 (llms_full)    Single HTTP GET of publisher-curated markdown bundle
  ↓ on manifest detected (ManifestDetected exception)
tier2 (llms.txt)     AnswerDotAI spec parser; fan-out parallel fetches (concurrency=8)
  ↓ on no usable links (EmptyLinksDetected)
tier3 (sitemap)      XML sitemap parser with recursive index flattening (depth=3)
  ↓
tier4 (docs)         4-phase crawler: seed enrichment → Crawl4AI discovery → httpx BFS → Playwright fallback
  ↓
tier5 (github)       GitHub API tree walk + parallel raw.githubusercontent.com GETs
                     ↓
                   post-processing: split monoliths (Source markers or H1), SHA256 dedup
                     ↓
                   storage: MinIO canonical + raw + vault-sentinelized
```

**Tunables**: monolith split threshold 50 KB; min section 300 B; tier concurrency 8-10; cancel poll 1s; lock TTL 35 min.

### SOTA gap analysis

| Layer | Current | SOTA frontier (May 2026) | Verdict |
|---|---|---|---|
| **Discovery priority** | llms_full > llms_txt > sitemap > docs > github | Same — frontier consensus pattern (Cursor, Windsurf, Claude Code, Aider all ship this) | **Match** |
| **Stealth browser** | Vanilla Playwright (in tier4) | Patchright (CDP-leak patches over Playwright; ~67% reduction in headless detection) OR Camoufox (Firefox-based, 0% headless detection on standard tests, passes Cloudflare/DataDome/Akamai) | **Lagging one gen** |
| **HTML extraction** | Direct markdown-write from response (no explicit extraction layer) | Trafilatura (F1 0.958 on independent benchmark) with `fallback=readability-lxml` built-in; heuristic extractors still beat neural on doc sites | **Missing** |
| **Dedup** | SHA256 byte-identical (in `post/service.py`) | SemHash semantic dedup (Model2Vec encoder, CPU-only, 1.8M docs in ~83s) catches paraphrased "Quickstart vs Getting Started" duplicates | **Lagging one gen** |
| **Incremental crawl** | None — every refresh re-downloads everything | ETag / If-None-Match + Last-Modified conditional GETs; Google formalized this Nov 2025 | **Missing** |
| **PDF/complex** | Not handled (markdown-only pipeline) | Docling (97.9% table extraction) — only needed if doc sources include PDFs | N/A |

### Top changes for Ingestion (ranked by ROI)

1. **Add ETag/If-None-Match conditional GETs** keyed per URL in MinIO sidecar metadata. ~80 LOC in `tiers/` + storage layer. **Single biggest practical win** — cuts re-ingest bandwidth + tokens to near-zero on unchanged docs. Sitemap-lastmod watermarking compounds the saving.
2. **Replace Playwright fallback with Patchright** (drop-in for vanilla Playwright in `tier4/`). ~30 LOC: same API, different package. Fixes silent block failures on Cloudflare/DataDome targets.
3. **Insert Trafilatura between fetch and markdown-write** with `fallback=readability-lxml`. ~100 LOC in tier2-4. Saves tokens (cleaner extraction) + improves downstream quality.
4. **Swap SHA256 dedup for SemHash** in `post/service.py`. ~80 LOC. Critical for framework docs full of paraphrased pages (every framework has "Quickstart", "Getting Started", "Introduction" that say the same thing).

### Sources (Ingestion)

- [State of llms.txt 2026 (Presenc AI)](https://presenc.ai/research/state-of-llms-txt-2026)
- [Camoufox vs Rebrowser vs Playwright fingerprint benchmark](https://evomi.com/blog/camoufox-vs.-rebrowser-vs.-stock-playwright-a-fingerprint-benchmark)
- [Anti-detect browser benchmark 2026 — Ian L. Paterson](https://ianlpaterson.com/blog/anti-detect-browser-benchmark-patchright-nodriver-curl-cffi/)
- [Trafilatura Evaluation docs](https://trafilatura.readthedocs.io/en/latest/evaluation.html)
- [2,000-Page Web Content Extraction Benchmark](https://murroughfoley.com/web-content-extraction-benchmark/)
- [SemHash — GitHub (MinishLab)](https://github.com/MinishLab/semhash)
- [Google crawling docs Nov 2025 update](https://ppc.land/google-updates-crawling-infrastructure-documentation-with-new-technical-details/)

---

## 2. PLANNER

### Current state (`apps/fastapi/domains/dd/planner/`)

**8-node LangGraph LITA-pattern hybrid**, all wired, all `IMPLEMENTED`. Drift from the 10-node sketch in [`PLANNER-ARCHITECTURE-2026-05-17.md`](./PLANNER-ARCHITECTURE-2026-05-17.md) is intentional (cache_lookup removed in favor of client-side thread reuse + LangGraph ainvoke-None skip; dedup deferred; validate merged into reduce).

```
START → corpus_load → embed_corpus → off_topic → cluster → refine → label → reduce → plan_write → END
        (classical)   (NIM embed)    (LLM)       (UMAP+    (LLM     (LLM    (LLM   (classical)
                                                  HDBSCAN)  big)     big)    big)
```

LITA split (classical foundation + LLM where it counts):
- **Classical**: corpus_load, embed_corpus, off_topic prefilter, cluster, plan_write
- **LLM-heavy**: refine (boundary docs only, ~20% corpus), label (KeyLLM-style USC vote), reduce (4-12 chapter merge with Self-Refine pass)

### SOTA gap analysis

| Node | Current | SOTA (May 2026) | Verdict |
|---|---|---|---|
| `embed_corpus` | `llama-nemotron-embed-1b-v2` 2048-dim | MTEB v2 leaders: Gemini Embedding 2, Qwen3-Embedding-8B (70.58), Harrier-OSS 27B (74.3), Linq-Embed-Mistral, Jina-v4. Llama-Nemotron-1b lags by ~6-9 pts on retrieval | **Lagging** |
| `cluster` | UMAP + HDBSCAN | k-core decomposition (Hossain & Sariyuce, Mar 2026) — deterministic, density-aware, linear-time, reproducible (modularity-on-sparse-graphs admits exponentially many near-optimal partitions = non-reproducible) | **Lagging** (reproducibility) |
| `off_topic` | LLM judge per doc + cross-encoder rerank on boundary band | jina-reranker-v3 (0.6B, Qwen3-backed, 131k ctx, BEIR 61.94) — listwise rerank of up to 64 docs in one causal pass; replaces both cross-encoder *and* LLM judge | **Lagging** |
| `refine` | Self-Refine (1 pass) | Socratic Self-Refine (SSR, arXiv 2511.10621, ICLR 2026 review) — decomposes outputs into (sub-Q, sub-A) pairs with step-level confidence, only repairs diagnosed unreliable sub-steps | **Lagging** |
| `label` | KeyLLM-style w/ c-TF-IDF candidates + 3-sample USC vote + Round 2 sibling-aware | Still competitive; SOTA layered: BERTopic c-TF-IDF → LLM rewrite + Chain-of-Layer (CIKM 2024) for hierarchical relations | **Adequate** |
| `reduce` | Clio meta-clustering + Self-Refine + coverage repair | **HippoRAG 2 + SurveyG hybrid** — Personalized PageRank over dual-node KG (passage + phrase); hierarchical citation graph (Foundation/Development/Frontier layers); explicitly built for hierarchical taxonomy synthesis from technical corpora | **Lagging** (biggest gap) |
| `plan_write` | Classical sanitize + persist | WriteHERE (EMNLP 2025 oral) interleaves recursive decomposition+execution; OmniThink uses Information Tree + Conceptual Pool. Both beat one-shot emit on technical reports | **Lagging** |

### Top changes for Planner (ranked by ROI)

1. **Swap embedding model** to NV-Embed-v3 / Qwen3-Embedding-8B. Config change only — ~10 LOC if the NIM API exposes it (verify availability first). MTEB v2 +6-9 pts on retrieval = better cluster geometry downstream.
2. **Replace `off_topic` LLM-judge + cross-encoder with jina-reranker-v3 listwise**. ~50 LOC. One model, one call per boundary batch of 64, BEIR 61.94. Eliminates the LLM-judge round-trip on the boundary band.
3. **Adopt Socratic Self-Refine in `refine` node**. ~120 LOC. Decompose plan into (sub-Q, sub-A) pairs; only repair diagnosed broken sub-steps. Aligns with [`feedback_kd_quality_over_speed`](../.claude/projects/-home-rafaelcoelho-Workbench-COELHONexus/memory/feedback_kd_quality_over_speed.md) — more iterations on the broken parts, none on the good parts.
4. **The biggest gap: adopt HippoRAG 2 / SurveyG hybrid as the `reduce` backbone**. ~500 LOC + dependency on a KG store (NetworkX in-memory might suffice for 100-2000 docs; FalkorDB if persistence wanted). Converts Planner from "semantic clustering pipeline" → "structure-aware taxonomy builder." Framework docs have citation-like structure (API references, decorator wraps, middleware composition) that pure embedding clustering cannot see. The Foundation/Development/Frontier hierarchy maps directly onto framework docs (e.g. FastAPI: ASGI/Pydantic/Starlette → routing/dependencies → lifespan/websockets/background-tasks).

### Sources (Planner)

- [Core-based Hierarchies for Efficient GraphRAG (Hossain & Sariyuce, Mar 2026)](https://arxiv.org/abs/2603.05207)
- [E²GraphRAG (May 2025)](https://arxiv.org/abs/2505.24226)
- [HippoRAG 2 — NeurIPS 2024](https://github.com/OSU-NLP-Group/HippoRAG)
- [SurveyG: hierarchical citation graph (Oct 2025)](https://arxiv.org/abs/2510.07733)
- [SSR — Socratic Self-Refine (Nov 2025)](https://arxiv.org/abs/2511.10621)
- [WriteHERE (EMNLP 2025 oral)](https://arxiv.org/abs/2503.08275)
- [OmniThink (EMNLP 2025)](https://arxiv.org/abs/2501.09751)
- [jina-reranker-v3 (Sep 2025)](https://arxiv.org/abs/2509.25085)
- [GraphRAG-Bench (ICLR 2026)](https://arxiv.org/abs/2506.05690)
- [MTEB v2 leaderboard 2026 overview](https://awesomeagents.ai/leaderboards/embedding-model-leaderboard-mteb-march-2026/)
- [Clio (Anthropic, Dec 2024)](https://arxiv.org/abs/2412.13678) — current backbone, still good for flat hierarchies

---

## 3. SYNTH

### Current state (`apps/fastapi/domains/dd/synth/`)

**6-node LangGraph**, runs per-chapter, all wired, all `IMPLEMENTED`:

```
START → outline_sdp → digest_construct → sawc_write → checklist_eval → mgsr_replan → render_audit_write → END
```

Matches [`SYNTH-ARCHITECTURE-SOTA-2026-05-18.md`](./SYNTH-ARCHITECTURE-SOTA-2026-05-18.md) exactly. Per-node mapping to published SOTA:

| Node | Paper backing | Status |
|---|---|---|
| `outline_sdp` | SurveyGen-I PlanEvo (arXiv 2508.14317, Aug 2025) | Current SOTA |
| `digest_construct` | LLMxMapReduce-V3 (arXiv 2510.10890, Oct 2025) | Current SOTA |
| `sawc_write` | SurveyGen-I SAWC + MAMM-Refine (arXiv 2503.15272, Mar 2025) | Current SOTA |
| `checklist_eval` | RefineBench (arXiv 2511.22173, Nov 2025) + Prometheus-2 | Current — see AutoChecklist gap below |
| `mgsr_replan` | CoRefine confidence halting (arXiv 2602.08948, Feb 2026) | Current — see naming gap below |
| `render_audit_write` | VeriCite (arXiv 2510.11394) + Verbatim Failures (arXiv 2601.03640, Jan 2026) confirms sentinel approach remains necessary | Current SOTA |

**Deferred to v2** (documented as such):
- MGSR loop-back (`StateGraph` has no cycle back to sawc_write — single-pass v1)
- Best-seen rescue (will use `argmax(checklist_pass_rate)` across iterations)
- Hash-recall reward → bandit feedback

### SOTA gap analysis

The Synth spec was written 2026-05-18. **Three papers post-date the spec** and are relevant:

| Paper | Date | What it adds |
|---|---|---|
| AutoChecklist | Mar 2026 (arXiv 2603.07019) | Unified checklist generator/scorer toolkit; per-chapter adaptive criteria vs hand-rolled ~10 criteria. Cost: 1 extra LLM call per chapter. Upside: a 14-section ML chapter shouldn't have the same checklist as a 4-section CLI cheatsheet. |
| Foundations of Global Consistency Checking | Jan 2026 (arXiv 2601.13600) | Minimal Unsatisfiable Subset (MUS) detection over noisy LLM oracle answers. Solves "section 3 says async, section 7 says sync." |
| Lost in Stories | Mar 2026 (arXiv 2603.05890) | Taxonomy of consistency bugs in long-form. Gives the criteria list for cross-chapter coherence. |

The single biggest **architectural** gap (acknowledged in the synth spec's defect table): **no cross-chapter coherence pass**. CaM-Writing memory ledger handles within-chapter cross-section memory, but ch02 prose can still contradict ch04 prose when both are reasonable in isolation.

### The MGSR naming question

**MGSR is homegrown.** No paper called "Memory-Guided Structure Replanner" exists. The closest published equivalent for what MGSR actually does (typed structural-replan actions on a DAG outline between Self-Refine iterations) is **SurveyGen-I PlanEvo §3.1 — "Dynamic Outline Evolution"**. Recommendation: rename `mgsr_replan` → `planevo_replan` and cite SurveyGen-I as the architectural reference. MGSR as branding is fine internally; the citation should be PlanEvo.

### Top changes for Synth (ranked by ROI)

1. **Add AutoChecklist generator** in front of `checklist_eval`. ~150 LOC. One extra LLM call per chapter to produce per-chapter adaptive checklist criteria. Same Prometheus-2-style backend for scoring.
2. **Add LongWriter-Zero-style reward shaping** to `sawc_write`'s Best-of-N selector. ~100 LOC. Length-target adherence + formatting compliance + structural coherence + citation density as a deterministic ranker over the N=3 SAWC drafts. Augments (doesn't replace) MAMM-Refine's critic. **Per the user's `feedback_kd_quality_over_speed`**, this is exactly the pattern — better selection over more drafts at higher token cost.
3. **Add cross-chapter coherence pass** as a new LangGraph that runs after all per-chapter graphs finish. ~200 LOC. Uses Foundations of Global Consistency Checking (MUS detection) + Lost in Stories taxonomy. Output: structured `cross_chapter_actions` (rephrase|merge|annotate) emitted to a final patch graph. Computational cost: ~20 LLM calls per 12-chapter framework guide (only DAG-adjacent chapter pairs, not full O(N²)).
4. **Rename `mgsr_replan` → `planevo_replan`** and update citations to SurveyGen-I §3.1. Trivial — ~20 LOC.

### Sources (Synth)

- [SurveyGen-I (arXiv 2508.14317)](https://arxiv.org/abs/2508.14317) — the canonical citation for outline_sdp + sawc + planevo (current mgsr)
- [LLMxMapReduce-V3 (arXiv 2510.10890)](https://arxiv.org/abs/2510.10890)
- [MAMM-Refine (arXiv 2503.15272)](https://arxiv.org/abs/2503.15272)
- [RefineBench (arXiv 2511.22173)](https://arxiv.org/abs/2511.22173)
- [CoRefine (arXiv 2602.08948)](https://arxiv.org/abs/2602.08948)
- [AutoChecklist (arXiv 2603.07019, Mar 2026)](https://arxiv.org/abs/2603.07019) — **post-spec, recommended addition**
- [Foundations of Global Consistency Checking (arXiv 2601.13600)](https://arxiv.org/abs/2601.13600) — **post-spec, biggest gap fix**
- [Lost in Stories (arXiv 2603.05890)](https://arxiv.org/abs/2603.05890) — **post-spec**
- [Verbatim Transcription Failures (arXiv 2601.03640, Jan 2026)](https://arxiv.org/abs/2601.03640) — confirms vault sentinel pattern still necessary
- [LongWriter-Zero (arXiv 2506.18841)](https://arxiv.org/abs/2506.18841)

---

## Ranked recommendations across all three pipelines

By **ROI per LOC** for free-tier framework-doc workloads. Each ships independently behind its own feature flag.

| # | Pipeline | Change | LOC | Win |
|---|---|---|---|---|
| 1 | Ingestion | ETag/If-None-Match conditional GETs | ~80 | Re-ingest cost → near-zero on unchanged docs |
| 2 | Ingestion | Playwright → Patchright (stealth) | ~30 | Fixes silent Cloudflare/DataDome blocks |
| 3 | Synth | Rename `mgsr_replan` → `planevo_replan` | ~20 | Citation hygiene; cite SurveyGen-I §3.1 |
| 4 | Ingestion | Trafilatura extraction layer | ~100 | Cleaner markdown, fewer tokens downstream |
| 5 | Planner | Swap embedding model (NV-Embed-v3 / Qwen3-Embedding-8B) | ~10 + verify NIM exposes it | +6-9 MTEB v2 pts on retrieval |
| 6 | Ingestion | SHA256 → SemHash semantic dedup | ~80 | Dedupes paraphrased pages (every framework's "Quickstart" vs "Getting Started") |
| 7 | Synth | Add AutoChecklist generator before `checklist_eval` | ~150 | Per-chapter adaptive criteria |
| 8 | Planner | Cross-encoder rerank + LLM judge → jina-reranker-v3 listwise | ~50 | One call replaces two; BEIR 61.94 |
| 9 | Synth | LongWriter-Zero reward shaping in `sawc_write` Best-of-N | ~100 | Deterministic Best-of-N ranker |
| 10 | Planner | Adopt SSR (Socratic Self-Refine) in `refine` node | ~120 | Repair only diagnosed broken sub-steps |
| 11 | Synth | Cross-chapter coherence pass (new LangGraph) | ~200 | **Biggest synth gap** — no current cross-chapter consistency |
| 12 | Planner | Adopt HippoRAG 2 / SurveyG hybrid as `reduce` backbone | ~500 | **Biggest planner gap** — structure-aware taxonomy vs flat clustering |

## What we are NOT recommending (and why)

| Idea | Why skip |
|---|---|
| **Multi-agent orchestration** (researcher/planner/critic/synthesizer) | Would win at 5000+ doc scale across multiple domains; doesn't justify complexity at 100-2000 docs/framework |
| **Full DSPy migration** | Instructor + LiteLLM + bandit already covers structured-output + routing surface |
| **Constitutional-AI multi-principle critique** | Checklist (RefineBench-style) is simpler and has direct empirical backing |
| **AST-level code grounding** | Overkill for documentation; not in any doc-synth literature |
| **CodeAct executable feedback** | Useful for code generation, not prose synthesis |
| **Streaming / online clustering** for 10K+ pages | Stress-test current design first; falls out as optimization if needed |
| **Hierarchical clustering** (mega-clusters → sub-clusters) | Over-engineering for 100-2000 docs |
| **Deep-Reporter multimodal extension** | Out of scope for markdown docs |

## Honest verdict

The user has built three pipelines that are **on or near the published frontier as of May 2026**. The deltas above are surgical upgrades — each shippable independently behind its own flag and OTel signal. There is **no architectural rewrite needed** anywhere.

The two genuinely big-effort items (Planner #12 HippoRAG 2 / SurveyG, Synth #11 cross-chapter coherence) address concrete gaps in the published literature post-2026-05-18, not speculative perfectionism. Both should wait until the LLM-rotator-with-FGTS-VA shadow data confirms the lower-effort items above have shipped cleanly.

Pair this with the rotator activation already shipped (FGTS-VA live as of 2026-05-23 per [`KD-ROTATOR-BANDIT-SOTA-2026-05-23.md`](./KD-ROTATOR-BANDIT-SOTA-2026-05-23.md)) and the order is clear: stabilize the bandit signal, then iterate on the highest-ROI ingestion/planner/synth deltas above.
