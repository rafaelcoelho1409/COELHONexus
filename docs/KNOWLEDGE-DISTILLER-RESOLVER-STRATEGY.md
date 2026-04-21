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
