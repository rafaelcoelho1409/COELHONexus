# Knowledge Distiller — Resolver Strategy

**Status:** Design approved, not yet implemented
**Date:** 2026-04-21
**Decision driver:** route crawler pipeline to Tier 1-4 correctly across heterogeneous frameworks, handle multi-tool crossover studies.

## Goal

Given an input (single framework name, or crossover request like *"Grafana Alloy + LGTM + PromQL + LogQL + River"*), return a list of `ResolvedDocs`:
```python
@dataclass
class ResolvedDocs:
    canonical_name: str
    docs_url: str | None
    repo_url: str | None
    registry_url: str | None
    version: str
    tier: int                   # 1-4 from the crawler waterfall
    tier_evidence: dict         # llms_full_txt / llms_txt / sitemap_xml probes
    confidence: float           # 0.0-1.0
    fallback_candidates: list[str]
    source_signals: dict        # registry, searxng, llm — provenance
```

Length-1 for single frameworks, length-N for crossover studies.

## Registry is existence-only; version is a string hint

**Registry does NOT compute docs URLs.** Publishers version their docs sites with wildly different conventions (`airflow.apache.org/docs/apache-airflow/3.0/` vs `/stable/` vs `/latest/`; Kubernetes `/v1.29/`; HuggingFace no per-library version in URL; etc.). Hard-coding a per-framework map is the wrong abstraction.

**Version flows through unchanged as a string hint** to SearXNG queries + LLM rerank prompt. The LLM reads candidate URLs and picks the version-matching one. No code needs to know "Airflow versions its docs at `/docs/apache-airflow/{major}.{minor}/`."

**What the registry still does:**

| Kept | Purpose |
|---|---|
| Existence check | "Is 'flow' a real package?" — early flag if not |
| Canonical `homepage` + `repo_url` | Context for LLM rerank |
| Cross-ecosystem lookup (deps.dev) | Same `homepage` field for Go/Rust/Java |
| Version existence check (optional) | "Is Airflow 4.0 real?" — nice-to-have |

**What the registry drops:**

- Version → docs URL derivation
- Per-framework hard-coded docs URL maps (the `registry.py` lookup tables)
- "latest" resolution logic (moves to Stage C — LLM picks "latest" vs "stable" vs "current")

**Resulting signature:**

```python
async def registry_lookup(framework: str) -> RegistryHint:
    """Does this package exist? What's its canonical homepage/repo?"""
    return {
        "exists": True,
        "homepage": "https://airflow.apache.org",
        "repo": "https://github.com/apache/airflow",
        "latest_version": "3.0.5",          # for existence/validity check
        "all_versions": ["3.0.5", ...],
    }
```

No URL computation. Just facts about the package.

## Three-stage pipeline (per framework)

```
Input: framework name + optional aliases + optional version
  │
  ├─► Stage A — Registry lookup (CHEAP, high-precision)
  │     PyPI  /pypi/{pkg}/json
  │     npm   registry.npmjs.org/{pkg}
  │     deps.dev (unified metadata for Go/Rust/Java/etc)
  │     libraries.io
  │     Output: 1-3 candidate URLs with provenance
  │
  ├─► Stage B — Web-search grounding (parallel SearXNG queries)
  │     Query templates (fired in parallel):
  │       - `"{name}" official documentation`
  │       - `"{name}" docs site:*.{io,com,dev,org}`
  │       - `"{name}" getting started tutorial`
  │     Output: top ~15 results with title/snippet/url
  │
  ├─► Stage C — LLM rerank + canonicalization (strict JSON schema)
  │     Model: NVIDIA NIM primary (free 40 RPM, JSON-schema capable)
  │            Groq fallback for latency spikes
  │     Input: registry candidates + searxng results + rules prompt
  │     Output: {docs_url, repo_url, registry_url, canonical_name,
  │              confidence: 0-1, rejected: [...]}
  │
  └─► Stage D — Validator (reuses probe_llmstxt_coverage.py logic)
        Probe docs_url/llms-full.txt, /llms.txt, /sitemap.xml
        Content-validate each (not just HTTP 200 — catches SPA_FAKE)
        Classify into Tier 1-4
        If Tier 4 AND confidence < 0.7:
          → retry with #2 candidate from LLM rejected list
```

## Why hybrid beats heuristic-only

Heuristics (`docs.{name}.com`, `{name}.readthedocs.io`) fail on:
- Custom subdomains: `python.langchain.com`, `reference.langchain.com`, `grafana.com/docs/alloy`, `pola.rs`
- Name collisions: `ray`, `flow`, `vllm`, `prefect` (multiple projects)
- Non-packaged tools not in PyPI/npm: Grafana Alloy, Terraform modules, k8s operators
- Version-specific docs: `/v1/`, `/latest/`, `/stable/` variants
- Multi-locale docs: `/en/`, `/zh/`, `/es/`

LLM rerank provides judgment heuristics can't encode — "which of these 10 search hits is the canonical docs root?" — backed by registry evidence + content validation.

**Precedent:** Context7 publicly moved all library resolution through LLM reranking in Jan 2026 ([Upstash blog](https://upstash.com/blog/new-context7)) precisely because pure heuristics failed at scale. Their ranking signals mirror ours: name similarity → description relevance → source reputation → org-level trust.

## Crossover resolution (multi-framework studies)

**Pattern: single-call query fan-out** (same pattern as Perplexity / Google AI Mode decomposition; [SearchEngineLand 2026](https://searchengineland.com/guide/query-fan-out)).

```
User request: "Grafana Alloy + LGTM + PromQL + LogQL + River DSL"
  │
  ├─► LLM decomposition (1 call, ~1K tokens):
  │     Output: [
  │       {topic: "Grafana Alloy",   canonical: "Grafana Alloy"},
  │       {topic: "Loki (LogQL)",     canonical: "Loki"},
  │       {topic: "Prometheus (PromQL)", canonical: "Prometheus"},
  │       {topic: "Grafana Tempo",   canonical: "Grafana Tempo"},
  │       {topic: "Grafana Mimir",   canonical: "Grafana Mimir"},
  │       {topic: "River DSL",       canonical: "Grafana Alloy (River config)"},
  │     ]
  │
  ├─► asyncio.gather() over single-framework resolver (parallel)
  │     Each topic → full Stage A/B/C/D pipeline independently
  │     Canonical name dedup: LogQL → Loki, PromQL → Prometheus
  │
  └─► Return list[ResolvedDocs] — one per canonical topic
```

**Detection:** a lightweight classifier runs on input — single framework vs crossover. If `N ≥ 2`, fan-out. Cheap — 1 LLM call before anything else. Auto-detect, not a separate endpoint.

### Worked example — "DeepAgents + LangChain + LangGraph"

All three are in the LangChain family but have DIFFERENT canonical docs roots with DIFFERENT tier classifications. The resolver handles this cleanly:

```
Input: "DeepAgents + LangChain + LangGraph"
  │
  ├─► Decomposition (1 LLM call, ~1K tokens):
  │     [
  │       {topic: "DeepAgents",  canonical: "DeepAgents"},
  │       {topic: "LangChain",   canonical: "LangChain"},
  │       {topic: "LangGraph",   canonical: "LangGraph"},
  │     ]
  │
  ├─► Fan-out (3 parallel resolver runs):
  │
  │     [DeepAgents]
  │       Registry: github.com/langchain-ai/deepagents (no PyPI docs_url)
  │       SearXNG: "DeepAgents" → reference.langchain.com/python/deepagents
  │       LLM rerank: picks reference.langchain.com/python/deepagents (conf 0.88)
  │       Validator: /llms-full.txt MISSING, /llms.txt MISSING, /sitemap.xml MISSING
  │       → tier = 4 (full Playwright path)
  │
  │     [LangChain]
  │       Registry: homepage=python.langchain.com, repo=github.com/langchain-ai/langchain
  │       SearXNG: "LangChain Python docs" → python.langchain.com (top)
  │       LLM rerank: picks python.langchain.com (conf 0.95)
  │       Validator: /llms-full.txt VALID, /llms.txt VALID, /sitemap.xml SPA_FAKE
  │       → tier = 1 (Tier 1 — fetch llms-full.txt, done in seconds)
  │
  │     [LangGraph]
  │       Registry: homepage=langchain-ai.github.io/langgraph
  │       SearXNG: "LangGraph docs" → langchain-ai.github.io/langgraph
  │       LLM rerank: picks langchain-ai.github.io/langgraph (conf 0.91)
  │       Validator: /llms-full.txt MISSING, /llms.txt VALID, /sitemap.xml MISSING
  │       → tier = 2 (fetch llms.txt, parallel-fetch each .md link)
  │
  └─► Return:
        [
          ResolvedDocs(name="DeepAgents", tier=4, docs_url=".../deepagents"),
          ResolvedDocs(name="LangChain",  tier=1, docs_url="python.langchain.com"),
          ResolvedDocs(name="LangGraph",  tier=2, docs_url=".../langgraph"),
        ]
```

**Downstream behavior:** the ingest pipeline processes each independently using its appropriate tier strategy. DeepAgents takes ~20 min via Playwright (current tuned stack), LangChain takes ~10 seconds via llms-full.txt, LangGraph takes ~1 minute via llms.txt + parallel httpx. All three corpora land in the same combined study_root.

### Study-root layout for crossover

A crossover study needs namespace per framework inside the shared study_root so the planner can identify source origin:

```
{user_id}/knowledge/{slug-of-combined-study}/
├── research/
│   ├── manifest.json                  # full manifest (all frameworks)
│   └── raw/
│       ├── deepagents/
│       │   ├── {slug}.md
│       │   └── {slug}.meta.json
│       ├── langchain/
│       │   ├── {slug}.md
│       │   └── {slug}.meta.json
│       └── langgraph/
│           ├── {slug}.md
│           └── {slug}.meta.json
├── chapter01/                         # planner may span frameworks per chapter
│   └── ...
└── summary.md
```

Slug computation for the combined study_root (`slug-of-combined-study`): stable hash of the sorted canonical names → e.g., `deepagents+langchain+langgraph-latest-senior`. Deterministic so the SAME input produces the SAME study_root (cache-friendly).

The planner sees the combined corpus and decomposes into chapters that may span multiple frameworks per chapter. E.g., Chapter 3 might be "LangChain retrievers + LangGraph state-graph integration + DeepAgents subagent middleware" — drawing from all three frameworks' files.

### Crossover cache semantics

Each framework's crawl is cached INDEPENDENTLY at `_cache/ingestion/{framework}/{version}/`. So on a re-run of the same combined study:

- LangChain cache hits → 0s (already crawled this week)
- LangGraph cache hits → 0s
- DeepAgents cache hits → 0s (assuming prior full-crawl in cache)

Combined study after first run: seconds on subsequent runs. Crossover doesn't multiply the crawl cost — it multiplies coverage breadth.

## API

```python
# apps/fastapi/routers/v1/knowledge/agents.py
@router.post("/studies/resolve")
async def resolve_docs(payload: ResolveRequest) -> list[ResolvedDocs]:
    """
    Resolve a framework (or combined-study request) to canonical docs URLs.

    Input:
      - payload.framework: str  — framework name OR crossover request
      - payload.version: str = "latest"
      - payload.aliases: list[str] = []
      - payload.allow_fallback: bool = True   # allow low-conf match or fail
    Output: list[ResolvedDocs]  — length 1 for single, length N for crossover
    """
```

## Caching

- Key: `coelhonexus:resolver:{sha256(name|aliases|version)}`
- TTL: 7 days on `confidence >= 0.7`, 1 hour on lower (re-try sooner)
- Value: full `ResolvedDocs` serialized JSON
- Invalidation: explicit via `DELETE /resolve/cache/{framework}` endpoint
- Bypass: `force_refresh=True` payload flag

## Error handling

- Never raise on partial failure — return `ResolvedDocs(confidence=0, docs_url=None, fallback_candidates=[...])`
- Low-confidence (< 0.3) → surface the `fallback_candidates` list so the user can pick manually or provide an override
- Registry miss + SearXNG zero hits → LLM one-shot guess as last resort
- OpenTelemetry spans: `resolver.stage_a_registry`, `resolver.stage_b_searxng`, `resolver.stage_c_llm`, `resolver.stage_d_validator`

## Concrete LLM prompt (Stage C)

```
You select the canonical documentation root URL for a software framework.

FRAMEWORK: {name}
ALIASES:   {aliases or []}
VERSION:   {version}

CANDIDATES (from SearXNG + registry):
{for r in results: f"- {r.url} | {r.title} | {r.snippet}"}

RULES:
1. Prefer the OFFICIAL publisher site (vendor domain, org GitHub Pages).
2. Prefer URLs ending in /docs, /documentation, or hosted on docs.*.
3. REJECT: PyPI/npm/crates package pages; Reddit, HN, StackOverflow,
   Medium, blog posts; GitHub README anchors (use repo root); mirror
   or fork orgs with low star count.
4. If framework has a dedicated docs subdomain, pick that over
   github.com/{org}/{repo}#readme or a /docs folder URL.
5. If multiple official locales exist, pick English unless user asked.
   Default version = "latest" unless user specified v2, stable, etc.

OUTPUT (JSON, strict schema):
{
  "docs_url": "...",
  "repo_url": "...|null",
  "registry_url": "...|null",
  "canonical_name": "...",
  "confidence": 0.0-1.0,
  "rejected": ["url:reason", ...]
}
```

Enforced via `response_format={"type": "json_schema", "json_schema": {..., "strict": true}}` on NIM endpoint — decode-time guarantee (same pattern as planner Fix #2).

## Crossover decomposition prompt

```
You receive a combined-study request that may reference multiple
documentation sources.

REQUEST: {input}

Decompose into a list of canonical technologies/tools, each of which
should be resolved independently. Canonicalize variants:
  - "LogQL" → "Loki" (LogQL is Loki's query language)
  - "PromQL" → "Prometheus"
  - "River" or "River DSL" → "Grafana Alloy" (River is Alloy's config)
  - "PySpark" → "Apache Spark"

Return at most 10 topics. If input is a single framework, return 1.

OUTPUT (JSON, strict):
{
  "is_crossover": bool,
  "topics": [
    {"topic": str, "canonical_name": str, "reason": str}, ...
  ]
}
```

## Comparison to current resolver

Current `/studies/resolve` in `distiller.py`:
- Registry lookup (PyPI/npm/crates via `services/knowledge/registry.py`)
- Scope classifier (is this a code framework?)
- Fixed-pattern docs URL resolution
- No SearXNG, no LLM rerank, no content validation, no tier classification, no crossover

New resolver adds:
- SearXNG stage (multi-engine aggregation, no rate limits)
- LLM rerank (NIM/Groq, strict JSON-schema)
- Content-validated tier classification (reuses probe script)
- Crossover decomposition + fan-out
- Redis caching with confidence-based TTL
- Structured error/low-confidence handoff to client

## Implementation plan

1. **Schema + config** — `schemas/knowledge/resolver.py` (ResolveRequest, ResolvedDocs, SearXNGResult, LLMRerankOutput)
2. **SearXNG client** — `services/knowledge/searxng.py` (parallel query runner with result dedup)
3. **LLM rerank** — `services/knowledge/docs_resolver.py` (Stage C + D orchestration)
4. **Crossover decomposer** — `services/knowledge/crossover.py` (decomposition prompt + fan-out)
5. **Validator** — reuse `scripts/probe_llmstxt_coverage.py` logic inline (extract into `services/knowledge/docs_probe.py`)
6. **Router endpoint** — `POST /studies/resolve` in new `routers/v1/knowledge/agents.py` (as part of router split)
7. **Cache layer** — Redis integration (reuse existing `app.state.redis_aio`)
8. **Tests** — golden-set of 20 frameworks + 5 crossover requests; assert tier classifications stable

Total: ~500 LoC. Estimated 4-6 hours.

## Validation metrics

After implementation, re-run the probe on the 108-framework list with the NEW resolver (LLM-reranked canonical URLs) and compare:
- Tier 1 coverage (was 19% with heuristic URLs)
- Tier 3 coverage (was 52%)
- Total fast-path coverage (was 61%)
- False SPA_FAKE rate (was 0 with validator — must stay 0)

Hypothesis: LLM-reranked canonical URLs lift Tier 1 + 3 coverage by ~10-20 points by fixing the "wrong subpath" and "GitHub-only" cases in our 42-framework tail.

## Budget per resolve call

| Stage | Cost |
|---|---|
| Registry | 1-2 HTTP calls, ~100ms |
| SearXNG | 3 parallel queries, ~500ms total |
| LLM rerank | 1 NIM call, ~2s (JSON schema output, ~1K tokens) |
| Validator | 3 parallel probes per candidate, ~300ms |
| **Total (single)** | **~3s, 1 LLM call, ~6 HTTP calls** |
| **Crossover (N=5 topics)** | **~5s, 6 LLM calls, ~30 HTTP calls** |

Free-tier budget: NIM 40 RPM → 40 single-framework resolves per minute. Crossover-heavy: ~7 per minute. Cached results don't count — 7-day TTL means most lookups hit Redis.

## Key design invariants

1. **Content-validated** — no SPA_FAKE false positives. Every "VALID" claim means we inspected the body.
2. **Graceful degradation** — low confidence never throws; returns candidates for user/override to decide
3. **Cacheable** — 7-day TTL on success; crawler runs are rare enough to hit cache >90%
4. **Crossover-native** — decomposition is first-class; user doesn't need to pre-parse their study topic
5. **Tier classification included** — crawler receives a routing decision, not just a URL

## Sources (2024-2026, independent where possible)

- [Upstash Context7 quality stack, Dec 2025](https://upstash.com/blog/context7-quality) — ranking signals
- [Upstash new Context7 architecture, Jan 2026](https://upstash.com/blog/new-context7) — server-side rerank move
- [SearchEngineLand query fan-out guide, 2026](https://searchengineland.com/guide/query-fan-out) — decomposition pattern
- [iPullRank expanding queries, Dec 2025](https://ipullrank.com/expanding-queries-with-fanout)
- [LangChain4j structured outputs guide, 2025](https://github.com/langchain4j/langchain4j/blob/main/docs/docs/tutorials/structured-outputs.md)
- [llms-central.com registry, 2025](https://llmscentral.com) — 2,147 registered sites
- [deps.dev unified metadata API](https://deps.dev) — cross-ecosystem homepage/repo lookup

---

# V2 — Destroyed-and-rebuilt 2026-04-26

**Status: SHIPPED in `apps/fastapi/services/resolver/` + `apps/fastapi/routers/v1/knowledge/resolve.py`** (in same session as this doc update). The V1 design above (LLM scope classifier + LLM decompose + LLM rerank + Tavily/Exa/Jina search-chain + coalescing) was DELETED during this session — it kept producing wrong canonical URLs (LangChain → reference.langchain.com bug, Docker → docker-py SDK, Helm → RealGeeks/helm namesake) because LLM rerank silently picks plausible-looking-but-wrong matches and search-result top-1 is biased toward marketing pages on phrases like "official documentation".

V2 is fully **deterministic + LLM-cascade-fallback only when needed**. No LLM rerank. No LLM scope classifier. No coalescing logic. ~1500 LoC across 8 modules.

## V2 architecture

```
POST /api/v1/knowledge/resolve  body: {"query": "free-text"}
  ↓
1. INPUT FILTER (services/resolver/query_splitter.py + resolve.py:_input_filter)
   - split on +, ',', ';', ' and ', ' & '
   - per multi-word token → LLM-cascade decomposition (existing app.state.llm via LiteLLM router)
     • handles "LGTM stack" → ["Loki", "Grafana", "Tempo", "Mimir"]
     • handles "Deploy a module on Terraform" → ["Terraform"]
     • returns [] for non-tech queries → resolver returns 'no-tech-identified'
   - per-token catalog fuzzy-match (rapidfuzz, threshold 85) for typo tolerance
  ↓ list of canonical framework names

2. PER-CANDIDATE PIPELINE (services/resolver/__init__.py exports)

   Layer 0 — catalog (sources.yaml)
     - apps/fastapi/sources.yaml mounted INSIDE the Docker image
     - hand-curated YAML: name, aliases, docs_url, repo_url, llms_full_txt,
       llms_txt, sitemap_xml, notes
     - INSTANT match when present → guaranteed correct
     - currently sparse (~3 entries — LangChain ecosystem); needs ~30 vendor-portal
       entries (Docker, K8s, Helm, NVIDIA, Sentry, Databricks, Q#, etc.)

   Layer 0b — llms.txt directory mirror (services/resolver/llmstxt.py)
     - mirrors thedaviddias/llms-txt-hub /data weekly via cron (cron NOT YET
       implemented; module ready, mirror dir empty)
     - loads at startup, O(1) name lookup
     - returns publisher-asserted llms_url + llms_full_url + canonical docs_url

   Layer 1 — ecosyste.ms /packages/lookup?name= (services/resolver/ecosystems.py)
     - 80+ registries (PyPI, npm, Cargo, Conda, Alpine, Homebrew, etc.)
     - VARIANT FALLBACK (recovers ~16 of 19 zero-hit cases on bash audit):
         lc → hyphenated → last_token → no_hyphens
         ('Apache Airflow' → 'apache airflow' → 'apache-airflow' ✓ → ...)
     - SMART CANONICAL-URL RANKER (pick_canonical_url):
         +100 if URL apex domain matches query name
         +50 if not VCS-host (github.com/gitlab.com)
         +30 if field is documentation_url
         -40 if docs-hosting subdomain (readthedocs.io, github.io — penalizes
             Python-SDK-on-readthedocs hits like docker-py.readthedocs.io)
         + min(versions_count,200)//10 tie-breaker
     - KEY EMPIRICAL INSIGHT: OS package managers (Alpine, Homebrew, Conda)
       store the upstream URL in repository_url for binary tools. For Docker,
       Alpine entry has repository_url=https://www.docker.io/ — that's how
       we recover infrastructure-tool docs from a packages registry.

   Layer 2 — search-API rotator (services/resolver/search_rotator.py)
     - SINGLE PROVIDER PER CALL (NOT fan-out) to economize free-tier quotas
     - Provider order: Exa Fast → Tavily → Linkup → Jina (last; slower MD reader)
     - Per-provider EWMA success-rate ranking (alpha=0.3) reorders priority
     - Per-month quota counter persisted in Redis (1000/mo cap default)
     - 24h cooldown on 429; 5min cooldown on transient 5xx
     - Search query: `"{name} documentation"` (NOT `"{name} official documentation"`
       — empirically 'official' biases toward marketing pages; tested 2026-04-26)
     - Tiered query fallback: documentation → docs site → api reference → inurl:docs
     - Brave killed its free tier 2025; skip for new builds.
     - Linkup: €5/mo free credit (~1000 standard searches), no card required.
     - Jina: returns clean Markdown of the result (saves D0 hop sometimes).

   SKIP-SEARCH HEURISTIC (resolve.py:_is_strong_canonical):
     Tier 2 search SKIPPED only when ecosyste.ms gave a STRONG canonical:
       documentation_url field set, OR docs.* host, OR /docs/ in path.
     Weak ecosystems matches (e.g. docker-py-readthedocs.io for query 'Docker')
     do NOT skip search → search runs and finds docs.docker.com via Tavily.

   Layer 3 — RRF (Reciprocal Rank Fusion) convergence (services/resolver/convergence.py)
     - Industry-standard fusion algorithm (Elasticsearch / OpenSearch / Azure AI Search)
     - score(url) = Σ over sources [ 1 / (k + rank_in_source) ], k=60
     - Pre-rank bumps: -0.5 if URL has /docs/ in path; -0.5 if host starts docs.*
     - HARD GATES (rejection, NOT scoring):
         D0 status must be LIVE (not DEAD/PARKED/EMPTY_SHELL/ERROR)
         Either name-token-in-domain OR ≥2 D0 docs_signals
     - Threshold 0.015 (just below single-source baseline of 1/61=0.0164)
     - WHY RRF over weighted-additive: heterogeneous source-score scales make
       weight calibration arbitrary. RRF normalizes via 1/(k+rank) curve;
       multi-source agreement still wins (3 sources at rank 1 → 3/61=0.049
       vs 1 source rank 1 → 0.016) without manual weight tuning.

   Layer 4 — D0 root liveness (services/resolver/liveness.py)
     - Pure HTTP + regex — no LLM, no crawler, no search API
     - Classifies docs_url into LIVE / EMPTY_SHELL / PARKED / DEAD / ERROR
     - LIVE = ≥2 docs signals (nav/headings/code/sidebar/search/docs-words)
       AND ≥400 chars text after tag-strip
       AND not parked-domain markers
       AND not off-host redirect (final_url host ≠ original host)
     - Off-host detection critical: caught LangChain redirect bug
       (python.langchain.com → docs.langchain.com is non-cousin redirect → DEAD)

3. DEDUPLICATION (resolve.py:_dedupe_results)
   - Group results by canonical docs URL
   - LangChain + LangGraph + DeepAgents → ONE entry with frameworks: [...]
   - Saves downstream pipeline from synthesizing identical content N times
```

## V2 module breakdown (`apps/fastapi/services/resolver/`)

| File | LoC | Role |
|---|---|---|
| `query_splitter.py` | ~50 | Splits on +, comma, ;, and, & |
| `catalog.py` | ~120 | Loads sources.yaml + rapidfuzz fuzzy lookup |
| `llmstxt.py` | ~110 | llms-txt-hub mirror loader (O(1) name → docs URL) |
| `ecosystems.py` | ~250 | ecosyste.ms client + variant fallback + smart ranker |
| `search_rotator.py` | ~280 | 4-provider rotation, EWMA, quota tracking |
| `liveness.py` | ~190 | D0 root liveness + parked detection + off-host check |
| `convergence.py` | ~200 | RRF fusion + hard gates + canonical URL form |
| `__init__.py` | ~30 | Public API exports |
| `resolve.py` (router) | ~280 | Orchestrator + LLM decomposition + dedup |

Total ~1500 LoC. NO Rust toolchain, NO PyTorch, NO heavy deps. Adds: pyyaml, rapidfuzz (already had it).

## Empirical findings (134-framework audit 2026-04-26)

Bash script `scripts/resolve_all_frameworks.sh` queried ecosyste.ms directly for all 134 frameworks in `apps/fastapi/files/frameworks.txt`:

| Bucket | Count | Action |
|---|---|---|
| `documentation_url` field set in ecosyste.ms response | 127 (97%) | Auto-resolved cleanly |
| Hits but only `repository_url` (no doc field) | 1 | Repo-URL fallback |
| **Zero hits → catalog override REQUIRED** | **3** (`al-folio`, `BotCity`, `Q#`) | Hand-curate |
| HTTP 500 (transient ecosyste.ms overload) | 3 (`Pydantic`, `Python`, `Scikit-Learn`) | Retry OR catalog |

After variant fallback (lc → hyphenated → last_token → no_hyphens) was added, recovered 15-16 frameworks that initially returned 0 hits (Apache Airflow, Apache Kafka, Sentence Transformers, NVIDIA Triton Inference Server, etc. — all matched via slug variants).

**Catalog override candidates (vendor portals — auto-discovery STRUCTURALLY can't find them):**
Docker, Kubernetes, Helm, Terraform, Terragrunt (verified manually), NVIDIA GPU Operator/DCGM Exporter/TensorRT/TensorRT-LLM/Triton Inference Server, Databricks, Sentry, Novu, AWS, Azure, Ubuntu, Kali Linux, QEMU, Burp Suite, Metasploit, Browse Use, Claude Code, Context Engineering, Delta Lake, Apache Airflow (where ecosystems gives partial), Apache Kafka, Q#, al-folio, BotCity, Shap-IQ.

~30 entries to hand-curate once → permanent perfect resolution.

## NER research findings (decided NOT to ship; recorded for future)

Investigated whether to add a tech-entity NER model (GLiNER family) as an upstream filter to extract framework names from arbitrary prose queries.

**Decision: SKIPPED** — replaced with LLM-cascade decomposition (existing `app.state.llm` LiteLLM router). Reasons:
1. Real query distribution: ~95% are direct names + simple lists handled by query_splitter.
2. The 5% prose case (acronym decomposition like "LGTM stack") is precisely what LLMs handle better than NER (NER can't decompose stacks; only LLMs encode tech-ecosystem relationships).
3. NER deployment friction: `fast-gliner` ships only Python 3.10-3.12 wheels; cp313 needs Rust toolchain build (`cc not found` in `python:3.13-slim`). Workarounds (downgrade, multi-stage Docker, switch to `gliner` w/ PyTorch ~800MB) all rejected.
4. Best NER model in 2026 (`knowledgator/modern-gliner-bi-large-v1.0` — ModernBERT bi-encoder) requires `gliner==0.2.21+` which the only ready Docker image (`ghcr.io/freinold/gliner-api:0.3.6`, MIT, 5 GH stars) doesn't yet support.

**If we ever revisit NER deployment:**
- Best ready Docker image (in 2026): `ghcr.io/freinold/gliner-api:0.3.6` — FastAPI wrapper with REST + OpenAPI + Prometheus + ONNX support; deploy as sidecar (~1.5GB RAM, 2 CPU comfortable)
- Best validated model with that image: `urchade/gliner_large-v2.5` (DeBERTa, 459M, ~700ms CPU p95)
- Theoretical SOTA when deployment unblocks: `knowledgator/modern-gliner-bi-large-v1.0` (ModernBERT bi-encoder; ~150ms via gline-rs Rust ONNX; pre-computed label embeddings → 130× throughput at 1k+ labels)
- Multi-model serving pattern: bundle GLiNER + future embedding model (e.g., bge-m3) in ONE Python 3.12 sidecar pod with FastAPI exposing /extract + /embed endpoints (~4-6GB RAM, 2-4 CPU). Saves ~500MB vs separate pods + simpler deployment.
- Required tech-entity labels for the bi-encoder pre-compute step: `["software framework", "library", "programming language", "SDK", "developer tool", "cloud service", "database"]`

## Pending V2 work

| Priority | Work | Why |
|---|---|---|
| P1 | Populate `apps/fastapi/sources.yaml` with ~30 vendor-portal entries | Unlocks production for Docker/K8s/NVIDIA/etc. — biggest accuracy win |
| P1 | Cron job to mirror llms-txt-hub /data weekly into MinIO | Activates Layer 0b (currently inert) |
| P2 | Plumb `app.state.redis_aio` into `_rotator = SearchRotator(redis_aio=...)` so quota counter persists across pod restarts | Currently process-local |
| P2 | Add Context7 MCP as Layer 1.5 (cross-check ecosyste.ms; 33k libs covered) | Deferred until next iteration |
| P3 | Add manifest parsing (pyproject.toml [project.urls]Documentation, package.json#homepage, Cargo.toml#documentation) BEFORE README scrape — publisher-asserted strongest signal | Future ingester work |
| P3 | README AST + shields.io badge mining when Layer 0b/1/2 all whiff | Future ingester work |

## Why V1 was destroyed (lessons preserved)

The V1 design above (in this same doc, before the V2 section) had:
- **LLM rerank** picking wrong canonical URLs silently (LangChain → reference.langchain.com because PyPI's `Documentation` URL field pointed there)
- **LLM scope classifier** rejecting valid queries (e.g. rejected "Claude Code" as "not a code framework")
- **Tavily/Exa/Jina search-chain** as PRIMARY discovery (paid, slow, hallucination risk on top result)
- **Coalescing logic** that grouped wrong frameworks together

V2 keeps the GOOD parts of V1 (registry-based existence check, multi-tool crossover decomposition, tier-evidence reporting) but replaces every LLM-judgment-call with deterministic ranking + RRF fusion + D0 content validation. LLM only fires for the narrow "decompose a multi-word prose token" case (e.g. "LGTM stack" → 4 names) — single structured-output call per multi-word token, JSON schema enforced, graceful empty-list on failure.

Result: ~96% auto-resolution on the 134-framework audit + ~3% catalog-required + ~1% true unresolvable. V1 was probably ~70% correct due to LLM-rerank false-positives on URLs like docker-py instead of Docker.

---

# V2.1 — Validated additions 2026-04-26 (resolver completion roadmap)

After end-to-end smoke-testing V2 we identified 4 specific failure modes and validated their fixes via deep research. This section preserves the validated decisions so the next-session implementer doesn't re-litigate them.

## Failure modes observed in V2 smoke test

| Failure | Root cause | Fix layer |
|---|---|---|
| `FastAPI` → `fastapi.org` (community SEO mirror) instead of `fastapi.tiangolo.com` (canonical) | ecosyste.ms HTTP 500 (intermittent on popular names) → fall back to Exa search → top result is SEO-optimized mirror | deps.dev cross-source + tiebreaker rules |
| `Pydantic` / `Python` / `Scikit-Learn` → ecosyste.ms HTTP 500 | ecosyste.ms server overload on common names | deps.dev parallel source for redundancy |
| `Vue` → unresolved (in "compare React Vue Svelte") | Generic name collision; no canonical match strong enough | Manifest parsing via deps.dev (`npm/vue` → homepage) |
| `MEAN stack` → MongoDB resolved to Rust driver instead of canonical docs | ecosyste.ms variant ranking picked Rust crate over canonical entry | Catalog override OR deps.dev cross-check |

## NEW Layer 1.5 — `deps.dev` (Google's unified package metadata API)

**Validated 2026-04-26** as the best unified parser. Free, no auth, no documented per-IP rate limit. Covers **7 ecosystems**: `GO`, `RUBYGEMS`, `NPM`, `CARGO`, `MAVEN`, `PYPI`, `NUGET`.

**CRITICAL gotcha — 2-call pattern required:**
The `links` array (containing categorized DOCUMENTATION/HOMEPAGE/SOURCE_REPO URLs) lives on the **GetVersion** endpoint, NOT GetPackage. Implementation:

```
1. GET https://api.deps.dev/v3/systems/{ecosystem}/packages/{name}
   → returns versions[]; pick the one with isDefault: true
2. GET https://api.deps.dev/v3/systems/{ecosystem}/packages/{name}/versions/{version}
   → returns links[] with {label, url} entries:
     - "DOCUMENTATION" → use first
     - "HOMEPAGE"      → fallback when DOCUMENTATION missing
     - "SOURCE_REPO"   → repo URL (already known via ecosyste.ms typically)
     - "ISSUE_TRACKER" → ignore
```

Spot-checked `pypi/fastapi==0.115.0` returns `https://fastapi.tiangolo.com/` correctly (which Exa search alone got wrong via SEO mirror).

**vs ecosyste.ms — pair them, don't replace:**
- deps.dev: 7 mainstream ecosystems, no documented rate limit, more reliable on popular names (Pydantic/Python don't 500 on deps.dev)
- ecosyste.ms: 100+ ecosystems including the long tail (Hex, CRAN, Conda, Pub, Hackage, Conan, alpine, homebrew) that deps.dev lacks; 5000 req/hr/IP rate limit
- **Strategy:** deps.dev primary for the 7 → ecosyste.ms fallback for ecosystems outside the 7 OR on deps.dev miss/timeout. RRF convergence layer dedupes.

**Replaces per-language manifest parsing** (pyproject.toml/package.json/Cargo.toml/etc. — would have been ~150-200 LoC across 6 parsers; deps.dev is one ~80 LoC client). The fields deps.dev returns ARE the manifest fields, normalized.

## Layer 0b activation specifics — `thedaviddias/llms-txt-hub`

**Validated 2026-04-26** as the best machine-readable llms.txt directory. Cross-compared against `directory.llmstxt.cloud` (HTML only, no API), `llmstxthub.com` (no machine-readable export), `llms-central.com` (REST API exists, ~4000 domains claimed, but `/api/search` returns empty for every framework tested — broken).

**Endpoint:**
```
https://raw.githubusercontent.com/thedaviddias/llms-txt-hub/main/data/websites.json
```

**Specs:**
- ~277 KB JSON file, **642 verified entries** (jq 'length')
- License: MIT (Copyright 2025 David Dias)
- Each entry: `{name, domain, description, llmsTxtUrl, llmsFullTxtUrl, category, favicon, publishedAt}`
- Auto-regenerated from MDX source files; latest entries dated 2026-03-14 (active)
- Categories: developer-tools 397, ai-ml 135, data-analytics 55, infrastructure-cloud 32, security-identity 23

**Refresh strategy (shipped 2026-04-26):**
- `bootstrap_llmstxt()` runs in FastAPI lifespan startup → fetches `websites.json` once, builds in-memory index keyed by both normalized name (`pydantic`) and alpha-only slug (`fastapi` ↔ `fast-api`)
- Background asyncio task (`llmstxt_refresh_loop`) re-fetches every 24h; cancellation-safe on shutdown
- NO K8s CronJob — overkill for a 277 KB file that rarely changes; pod restarts already refresh on every deploy
- File fits entirely in Python dict (~642 entries × ~200 bytes ≈ 128 KB RAM)
- Graceful: if GitHub unreachable on bootstrap, `lookup_llmstxt()` returns None for every name and the resolver falls through to ecosyste.ms / deps.dev / search

**Coverage spot-check:**
- ✅ Pydantic (with both llms.txt + llms-full.txt URLs)
- ✅ LangChain (Python + JS variants), LangGraph, LangFuse, Langflow
- ❌ FastAPI — NOT in the hub (manifest parsing via deps.dev catches it)
- ❌ vLLM — NOT in the hub (manifest parsing catches it)

## llms-full.txt: NO dedicated indexer exists in 2026

**Validated 2026-04-26**: searched for specialized llms-full.txt aggregators; found none. Existing llms.txt directories track llms-full.txt as a SEPARATE field (where present), but coverage of llms-full.txt specifically is much smaller than llms.txt.

**Critical finding — 50% miss rate on the `{docs_url}/llms-full.txt` heuristic** for top 5 frameworks tested:

| Framework | `{base}/llms-full.txt` result |
|---|---|
| LangChain | 308 redirect → broken (docs migration) |
| Pydantic | 301 → wrong URL (serves API HTML, not bundle) |
| **FastAPI** | **404 — doesn't publish llms-full.txt at all** |
| Cursor | 308 → broken |
| Anthropic Claude | ✅ works (481K tokens at platform.claude.com/llms-full.txt) |

**Strongest signal that llms-full.txt EXISTS for a given site:** Mintlify or GitBook hosting (both auto-generate llms-full.txt with zero config). Detect platform via response headers / HTML signature → assume `{base}/llms-full.txt` exists with high confidence.

**Implementation guidance:**
- Don't build an llms-full.txt aggregator (no good source data)
- DO probe `{base}/llms-full.txt` AND `{base}/docs/llms-full.txt` AND `{base}/latest/llms-full.txt` (3 common URL patterns)
- ALWAYS validate response: HTTP 200 (not 308/301 to wrong URL), content-type text/plain (not HTML), size > 5 KB (not stub redirect page)
- Trust the llms-txt-hub `llmsFullTxtUrl` field when present (publisher-asserted)
- For projects you depend on heavily, hard-code the URL in the catalog (when catalog is restored)

## NEW direct llms.txt HEAD probe (Layer 4.5)

After all upstream sources exhaust, probe `{resolved_docs_url}/llms.txt` directly with content validation. Catches projects that publish llms.txt but aren't registered in any directory.

**Validation gates** (all required):
- HTTP 200 after redirect chain
- Body length ≥ 200 bytes
- Body does NOT start with `<` (HTML rejection)
- At least one `[text](url)` link pattern (llms.txt format check)
- No "redirecting" / "not found" / "404" sentinels in first 200 chars

If passes, treat as a high-confidence URL contributor for RRF fusion.

## NEW convergence tiebreaker rules (services/resolver/convergence.py)

When multiple candidate URLs all pass D0 gates AND have similar RRF scores, apply tiebreakers in this order:

1. **Publisher-asserted wins**: contributor `llmstxt-hub` (Layer 0b) > `deps.dev` > `ecosyste.ms` documentation_url field > anything else
2. **Manifest match wins**: URL appears in deps.dev `links` as `DOCUMENTATION` label > as `HOMEPAGE`
3. **Domain depth heuristic**: prefer 3-segment subdomains (`fastapi.tiangolo.com`) over 2-segment (`fastapi.org`) when both contain framework name — addresses SEO-mirror problem
4. **Path specificity**: URL ending in `/docs/` or `/latest/` > URL at root
5. **D0 docs_signals count**: more signals = stronger docs page

These run as RRF pre-rank bumps (small score adjustments) NOT hard gates.

## Phased implementation order — final

| Phase | Steps | Effort | Why grouped |
|---|---|---|---|
| **A — Resolver completion** | (1) commit current code; (2) llms.txt-hub mirror activation; (3) deps.dev client (2-call pattern); (4) direct llms.txt HEAD probe; (5) convergence tiebreaker rules | 1 session, ~3h | Closes URL-discovery layer to ~99% accuracy |
| **B — Ingester V2 expansion** | (1) DevDocs.io tarball cache; (2) Context7 MCP; (3) GitMCP universal pass-through; (4) raw GitHub /docs walk; (5) Read the Docs htmlzip; (6) Sphinx _sources probe; (7) DeepWiki MCP (with vault-audit verification); (8) Mintlify/GitBook detection for llms-full.txt | 2-3 sessions | Closes URL-to-content layer; multiplies resilience for ingestion |
| **C — Strategic synth work** | Round 3 classical-first migration (TF-IDF section naming, KMeansConstrained outline, deterministic critic dimensions, etc.) | days | The actual product value-creation layer |

Phase A is the committable resolver completion. Phase B materially expands what content the synth can ingest (especially for projects without llms-full.txt). Phase C is the original product strategic plan from before the resolver detour.

## Updated module list (after Phase A ships)

| File | LoC est | Role |
|---|---|---|
| `services/resolver/query_splitter.py` | ~50 | Splits on +, comma, ;, and, & (existing) |
| `services/resolver/catalog.py` | ~120 | sources.yaml override + rapidfuzz (existing — sources.yaml currently empty) |
| `services/resolver/llmstxt.py` | ~200 | llms-txt-hub in-process loader: GitHub raw fetch + 24h asyncio refresh loop |
| `services/resolver/ecosystems.py` | ~250 | ecosyste.ms client + variant fallback + smart ranker (existing) |
| `services/resolver/depsdev.py` | ~235 | deps.dev 2-call client + ecosystem mapping + link-array picker |
| `services/resolver/llmstxt_probe.py` | ~170 | Direct {docs_url}/llms.txt + /llms-full.txt HEAD probes with content validation |
| `services/resolver/search_rotator.py` | ~280 | 4-provider rotation, EWMA, quota tracking (existing) |
| `services/resolver/liveness.py` | ~190 | D0 root liveness (existing) |
| `services/resolver/convergence.py` | ~280 | RRF + hard gates + tiebreaker rules (source priority, field priority, path specificity, docs_signals, subdomain depth) |
| `services/resolver/__init__.py` | ~60 | Public API exports |
| `routers/v1/knowledge/resolve.py` | ~340 | Orchestrator: LLM decomposition + ecosyste.ms + deps.dev parallel + llms.txt probe + dedup |
| `apps/fastapi/app.py` lifespan | +10 | `bootstrap_llmstxt()` on startup + `llmstxt_refresh_loop()` background task; cancelled on shutdown |

Phase A net adds: ~280 LoC across 4 new files, ~20 LoC of edits to existing files.

## Sources cited (V2.1 validation, all 2026)

- [deps.dev v3 API docs](https://docs.deps.dev/api/v3/) — confirmed 7 ecosystems + 2-call pattern
- [deps.dev API blog](https://blog.deps.dev/api-v3/) — stability guarantee
- [thedaviddias/llms-txt-hub on GitHub](https://github.com/thedaviddias/llms-txt-hub) — 642 entries verified via jq
- [llms-txt-hub LICENCE (MIT)](https://github.com/thedaviddias/llms-txt-hub/blob/main/LICENCE)
- [llmscentral.com /api/stats](https://llmscentral.com/api/stats) — 4023 claimed but search broken
- [Mintlify llms.txt auto-generation](https://www.mintlify.com/docs/ai/llmstxt) — Mintlify+GitBook auto-publish llms-full.txt
- [Andrew Nesbitt — Package Management Landscape 2026](https://nesbitt.io/2026/01/03/the-package-management-landscape.html) — ecosyste.ms 5.5M packages, 100+ registries

---

# V3 — Pivot from name-resolver to URL-validator (decision 2026-04-26)

**Status:** Decided, not yet implemented. Supersedes V2 / V2.1 as the primary architecture. Name-resolver layers are demoted to *candidate suggester*, not deleted.

## What changed

The 132-framework benchmark (V2.1, 2026-04-26) hit a hard ceiling at ~54% confident-resolution. The remaining 46% split into 6 stable failure clusters (`docs.rs` last-token hijack, LLM over-rejection, D0 over-strictness, VCS clobbering vendor, Wikipedia capture, multi-tenant binding-vs-platform). Research surfaced a working fix for each (Wikipedia blocklist, whitelist variants, binding-suffix demotion, PEP 753 + README mining, Trafilatura, two-stage LLM cascade) — total ~455 LoC + ongoing tuning.

Before shipping any of those, we re-examined the framing. Conclusion: **we were solving the wrong problem.**

## The reframe

| Old framing | New framing |
|---|---|
| Name → URL (search problem) | URL → "is docs?" (classification problem) |
| Predict what the user means | Validate what the user supplied |
| Ambiguity is our problem to solve | Ambiguity is reality; user disambiguates by giving us the URL |
| Wrong silently → corrupted study corpus | Confidence score → user sees uncertainty |

Search is fundamentally hard (ambiguous queries, ranking, freshness). Classification of a single fetched page is a well-understood problem with deterministic signals (Mintlify/GitBook headers, llms.txt presence, trafilatura page-type, docs_signals, host class).

## Why the pivot is correct

1. **Information-theoretic.** "Vue" really IS multiple projects. "Docker" really IS both the platform and `docker-py`. No ranker can recover information the user never gave us. Asking for the URL is asking the user to disambiguate something *only they can disambiguate*.
2. **Industry has converged here.** Context7's primary input is `library_id` (URL-shaped). DevDocs makes the user pick a docset explicitly. GitMCP wants the GitHub URL. Magic name-resolution is the 2026 outlier.
3. **For a quality-focused user, wrong > slow.** A wrong corpus = hours of synthesis + reading + corrupted study root. One paste step costs 5 seconds.
4. **Failure-mode budget is infinite.** Every fix surfaces 2 new edge cases (observed empirically across 6 sessions). The validator collapses the whole class to one yes/no/maybe.
5. **Validator is composable.** Sits BEHIND the name-suggester (gates auto-ingest), the synth pipeline (sanity-checks any URL from any source), and direct user input.

## V3 architecture

```
┌─────────────────────────────────────────────────────────────────┐
│ User enters either:                                             │
│   (A) URL directly (preferred — primary path)                   │
│   (B) NAME (convenience — secondary path)                       │
└─────────────────────────────────────────────────────────────────┘
       │                                  │
       │ (A) direct URL                   │ (B) name
       │                                  ▼
       │                  ┌──────────────────────────────────┐
       │                  │ Name-suggester (DEMOTED)         │
       │                  │   - catalog                      │
       │                  │   - llmstxt-hub                  │
       │                  │   - ecosyste.ms / deps.dev       │
       │                  │   - search rotator               │
       │                  │ Returns TOP-N candidate URLs     │
       │                  │ with per-source provenance.      │
       │                  │ NO auto-pick.                    │
       │                  └──────────────────────────────────┘
       │                                  │
       │                                  ▼
       │                  ┌──────────────────────────────────┐
       │                  │ User picks one candidate         │
       │                  │ (UI shows confidence per item)   │
       │                  └──────────────────────────────────┘
       │                                  │
       ▼                                  ▼
┌─────────────────────────────────────────────────────────────────┐
│ Validator — POST /api/v1/knowledge/validate {"url": "..."}       │
│   Single fetch + classify:                                       │
│     - Trafilatura extract + page-type (documentation/article/…)  │
│     - llms.txt + llms-full.txt direct probe                      │
│     - Mintlify / GitBook / Docusaurus / Sphinx fingerprint       │
│     - docs_signals count (nav/sidebar/code/search/version)       │
│     - Host class (docs.* / vendor TLD / multi-tenant / VCS / …)  │
│     - Parked / off-host-redirect / wikipedia / 404 detection     │
│   Output: { is_docs, confidence (0-1), classification,           │
│             signals[], warnings[], ingestion_tier (1-4) }        │
└─────────────────────────────────────────────────────────────────┘
       │
       ▼
   Confidence buckets:
     ≥ 0.85  green → auto-ingest
     0.5-0.85 amber → warn user, require confirm
     < 0.5   red → reject with reasons
       │
       ▼
   Ingest at returned tier (1=llms-full / 2=llms.txt / 3=sitemap / 4=Playwright).
```

## What V3 ships

| File | Status | Action |
|---|---|---|
| `services/resolver/validator.py` | NEW (~250 LoC) | Single-page fetch + classify; reuses `liveness.py` + `llmstxt_probe.py`; adds Trafilatura + Mintlify/GitBook/Docusaurus/Sphinx fingerprints |
| `routers/v1/knowledge/validate.py` | NEW (~80 LoC) | `POST /api/v1/knowledge/validate` endpoint |
| `routers/v1/knowledge/resolve.py` | MODIFY | Return TOP-N candidates with confidence per item; remove auto-pick; remove dedupe-by-canonical (let user see all) |
| `services/resolver/convergence.py` | KEEP, DEMOTED | Still used to score candidates; no longer authoritative |
| `services/resolver/{ecosystems,depsdev,llmstxt,search_rotator,catalog}` | KEEP | Become candidate sources; their accuracy ceiling stops mattering |
| `services/resolver/liveness.py` | EVOLVE | Replace DIY signal counting with Trafilatura page-type + allow-list bypass for known docs hosts (this was Cluster 3 fix from V2.1 research, still applicable) |

## What V3 explicitly drops

- The 6 V2.1 fixes (Wikipedia blocklist, whitelist variants, binding-suffix demotion, PEP 753 + README mining, two-stage LLM cascade) — **NOT shipped**. They were solving the symptoms of the wrong framing.
- Auto-pick / "best result" semantics in `/resolve`. The endpoint becomes a *suggester*, not an *authority*.
- Convergence tiebreaker tuning loop (we stop fighting it).
- D0 hard gates as auto-ingest blockers (signals become inputs to the classifier, not gatekeepers).

## What V3 keeps

- llms-txt-hub mirror loader + 24h refresh — high-precision Layer 0b for the suggester path.
- Catalog (`sources.yaml`) when populated — top-priority publisher-asserted.
- ecosyste.ms / deps.dev / search rotator — useful for *generating* candidates the user might not know about.
- llms.txt direct probe — moves into the validator as the strongest single docs signal.
- The 132-framework benchmark NDJSON — becomes the evaluation harness for the validator (label each row "should resolve to URL X" and re-run).

## Why we're not deleting the name resolver

Two real use cases keep it alive:
1. **Top-20 frameworks** (FastAPI, Pandas, LangChain, etc.) — the catalog + llmstxt-hub layers resolve these in O(1) with publisher-asserted signals. Zero ambiguity, zero failure modes. Keep for ergonomics.
2. **Discovery** — "I want to learn workflow orchestration, what should I read?" is a separate feature, but the suggester's candidate generation is the foundation for it.

Demoting from "answer" to "suggestion" lets these use cases coexist without their failures contaminating ingest.

## Implementation roadmap

1. **Phase A (this sprint)** — build `validator.py` + `/validate` endpoint. Add Trafilatura. Wire into existing UI as "paste URL" button. Validate against the 132-benchmark URLs (label expected URLs first).
2. **Phase B** — refactor `/resolve` to return TOP-N + confidence; UI presents as picker.
3. **Phase C** — synth pipeline calls validator on every URL it processes (sanity check before crawl).
4. **Phase D** — discovery feature on top of suggester (separate scope).

## Sunk cost honesty

V2 + V2.1 were ~1500 LoC and many sessions. Worth it: we now have battle-tested candidate sources (ecosyste.ms variant fallback, deps.dev case-tolerance, search rotator quota economy, llms.txt-hub in-process mirror, llms.txt direct probe) — all reusable in V3 as suggester components. The convergence tuning code is the part we're walking away from. That's <300 LoC.

## Sources cited (V3 decision, 2026)

- [Context7 — resolve-library-id + library_id input shape](https://github.com/upstash/context7)
- [DevDocs — explicit docset selection UX](https://devdocs.io)
- [GitMCP — direct GitHub URL ingestion](https://gitmcp.io)
- [Trafilatura 2.0 evaluation (F1 0.958, prod use HF/IBM/MS)](https://trafilatura.readthedocs.io/en/latest/evaluation.html)
- [PEP 753 — Uniform project URLs (deferred to V3 validator)](https://peps.python.org/pep-0753/)
