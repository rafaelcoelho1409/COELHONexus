# Observability SOTA — LangFuse + OpenTelemetry on COELHO Nexus

**Date:** 2026-06-18
**Stack:** LangFuse v3 (self-host) + OpenTelemetry → Alloy → LGTM (Loki / Grafana / Tempo / Mimir)
**Primary purpose:** learning vehicle for LangFuse + OTel SOTA, with code organized as future reference. Maximize feature surface area of both tools across the three main features (DD, YCS, RR) without unnecessary bloat.

---

## 0. Goal & scope

- Wire LangFuse and OpenTelemetry into COELHO Nexus in a way that:
  1. exercises the **major feature set** of each tool (not just OTLP-as-sink),
  2. produces **clean, isolated, didactic code** that future-me can read top-to-bottom,
  3. respects the project's hard constraints — **free-tier only, no in-cluster inference, BYOK rotator**.
- Distribute the learning surface across the three features so each one demonstrates a different observability pattern:
  - **DD (Docs Distiller)** — multi-node LangGraph pipeline → prompt management, scores, datasets, regression evals.
  - **YCS (YouTube Channel Summarizer)** — async ingestion + Graph-RAG → `db.*` semconv, sessions/users, RAGAS-style evals.
  - **RR (Research Radar)** — DeepAgents + FastMCP federation → `gen_ai.agent.*` / `gen_ai.tool.*`, subagent traces, MCP tool spans.

---

## 1. State of the art — June 2026

| Area | What's true today | Implication |
|---|---|---|
| LangFuse OTLP | v3.22+ is OTLP-native; one OTel emit lands in Alloy (Tempo/Mimir/Loki) **and** LangFuse simultaneously. No SDK required for the trace path. | The current `apps/fastapi/infra/otel/exporters.py` dual-export is the correct shape — keep it. |
| GenAI semconv | `gen_ai.*` attributes are standardized but still in **Development** status. Opt in via `OTEL_SEMCONV_STABILITY_OPT_IN=gen_ai_latest_experimental`. | Use semconv constants, not custom strings, so the same trace renders correctly in LangFuse, Tempo, Datadog, etc. |
| Agent + tool spans | `gen_ai.agent.*` (`gen_ai.agent.name`, `gen_ai.agent.id`) and `gen_ai.tool.*` (`gen_ai.tool.name`, `gen_ai.tool.call.id`) are now spec'd. | RR's DeepAgents subagents and FastMCP tools should emit both. |
| LiteLLM v2 OTel | `LITELLM_OTEL_V2=true` produces one trace per request following gen_ai semconv. `langfuse_otel` callback adds LangFuse-specific extras (cost). | One env var + one callback replaces most manual per-call instrumentation in the rotator. |
| DeepAgents | First-class LangFuse `CallbackHandler` integration — subagents become nested traces automatically. | RR gets full agent-tree visibility from a 3-line wire-up. |
| Baggage | `BaggageSpanProcessor` is the recommended pattern for propagating `study_id` / `channel_id` / `digest_id` to every child span without kwargs threading. | One processor in `infra/otel/baggage.py`, used everywhere. |
| Sampling | Tail sampling at the **Alloy** layer (not SDK) — keep errors + slow > p95, sample fast 1%. Cost-controlled retention without losing high-signal traces. | Sampler stays simple in-app; Alloy collector config does the heavy lifting. |
| Stability target | `gen_ai.*` not yet stable — pin opt-in version, revisit each quarter. v1.36 is the transition baseline. | One constant in `infra/otel/semconv.py`. |

---

## 2. Already shipped (audit — do not rebuild)

| File | Provides |
|---|---|
| `apps/fastapi/infra/otel/service.py` | `init_otel()` bootstrap, idempotent, fork-safe for Celery (`init_otel_for_celery_worker`); auto-instruments httpx, redis, logging, Celery, FastAPI. |
| `apps/fastapi/infra/otel/exporters.py` | Dual export: Alloy (gRPC → Tempo) + LangFuse (HTTP/OTLP). |
| `apps/fastapi/infra/otel/filters.py` | Rate-limit collector-down log noise at startup. |
| `apps/fastapi/domains/llm/rotator/otel_metrics/` | KD-specific metrics: chapter outcomes, refiner iters, grader scores, audit ratios, study completion, classical patches. Mimir-ready PromQL examples in the module docstring. |
| `apps/fastapi/domains/llm/rotator/observability/` | Domain-level observability skeleton (domain/service/keys/params split). |
| `apps/fastapi/domains/{dd/synth,dd/planner,ycs}/runtime/observability/` | Per-domain observability folders already laid out. |
| `apps/fastmcp/middleware/telemetry.py` | Per-tool span emitting `mcp.tool.name`, `mcp.tool.args.keys` (intentionally **not** values — sensitive payload safety), `mcp.tool.error_type/msg`. |
| `apps/fastmcp/infra/otel/` | Mirror of FastAPI's OTel skeleton for the MCP process. |

**Gap analysis:** LangFuse is currently used as a **pure OTLP sink** — no SDK calls. That means no prompt management, no scores, no datasets, no evals, no annotation queues, no sessions/users. That's where 80% of the learning value lives.

---

## 3. LangFuse features to wire (max learning per hour)

Ranked by ROI for learning + practical project value.

| # | Feature | Use in DD / YCS / RR | First concrete ship |
|---|---|---|---|
| 1 | **Prompt management** (versioning, label-deploy, cache) | All three; start with the highest-churn prompts | Migrate `domains/dd/planner/nodes/chapter_propose/prompts.py` (most-iterated prompt in repo) |
| 2 | **Sessions + Users** | Group `study_id` (DD), Ask conversation (YCS), digest cycle (RR). `user_id` = tenant / channel_id / `default` | Single helper `infra/langfuse/sessions.py` — context-manager friendly |
| 3 | **Scores** | Dual-write existing grader dims (`record_grader_dim_score`) — trace UI shows them inline | Thin wrapper `infra/langfuse/scores.py` — accepts `(trace_id, name, value, comment)` |
| 4 | **Datasets** | Gold corpora per feature: small reference book (DD), 50 Q/A pairs (YCS), 20 known-good arxiv items (RR) | One uploader + one runner; fixtures under `observability/fixtures/` |
| 5 | **LLM-as-judge evaluations** | DD: faithfulness, citation accuracy, code density. YCS: RAGAS faithfulness / answer-relevance / context-precision. RR: novelty (Jaccard vs last N digests), implementability score | Judge = pinned high-quality NIM model through the rotator → $0 |
| 6 | **Annotation queues** (human review) | Route low-confidence planner `chapter_assign` items + refiner first-pass accepts | Showcases human-in-loop without inventing UI work |
| 7 | **Playground** | Pin a trace → tweak prompt → rerun via labels | Free as soon as prompts live in LangFuse |
| 8 | **Prompt experiments / variant labels** | A/B `production` vs `canary` on chapter_propose; auto-eval on the same gold dataset | One-time wiring, infinite reuse |
| 9 | **Cost ledger** | LiteLLM `langfuse_otel` callback emits `gen_ai.usage.*` + cost; LangFuse dashboards aggregate per-tenant/per-model | Free with the LiteLLM flag |
| 10 | **Trace sampling rules** (env-aware) | `dev = 100%`, `prod = 10%`, override 100% on error | One sampler class + env var |

---

## 4. OpenTelemetry features to wire

Ranked by ROI for the LGTM half of the stack (where LangFuse is weak).

| # | Feature | Where it pays off |
|---|---|---|
| 1 | **`gen_ai.*` semconv conformance** in `domains/llm/rotator/observability/` | Portability + LangFuse UI alignment + future-proof against semconv stabilization |
| 2 | **`gen_ai.agent.*` + `gen_ai.tool.*`** spans in RR DeepAgents + FastMCP middleware | Agent/tool spans render as agents/tools in any backend (LangFuse, Tempo, Datadog) |
| 3 | **Baggage propagation** (`study_id`, `channel_id`, `digest_id`, `arm_name`, `tenant`) via `BaggageSpanProcessor` | Every child span auto-tagged — no kwargs threading through 10 layers |
| 4 | **Tail sampling at Alloy** (keep errors + slow > p95, sample fast 1%) | Cost-controlled retention; classic SRE pattern |
| 5 | **Histogram exemplars** linking Mimir histograms back to Tempo traces | "Click p95 spike → exact trace" — flagship demo dashboard |
| 6 | **Manual `db.*` spans** for Qdrant hybrid search, Neo4j queries, ES hybrid queries | YCS is the showcase; uses `db.system`, `db.operation`, `db.qdrant.collection_name`, etc. |
| 7 | **Span events** (not spans) for transient fine-grained steps (bandit arm pick, rate-limit wait) | Cheaper than spans, still queryable as `span.event.*` |
| 8 | **Resource attrs from git SHA + Helm chart version** in `build_resource()` | "Which deploy regressed?" diff in Grafana annotations |
| 9 | **OTel logs (signal)** for structured prompt-mask events + audit decisions | Logs share trace_id with spans via existing `LoggingInstrumentor` |
| 10 | **SLO recording rules + burn-rate alerts** on `kd_chapter_outcome` and `gen_ai.client.operation.duration` | Real SLO practice — reusable pattern for any future LLM service |

---

## 5. Per-feature showcase mapping

Each feature is intentionally chosen to demonstrate a distinct pattern. Reading all three end-to-end should leave a complete mental model of LangFuse + OTel.

### 5.1 DD — Docs Distiller (LangGraph + Celery)

**Demonstrates:** prompt management, scores dual-write, datasets, LLM-judge evals, regression workflow.

- Each LangGraph node = one span; `study_id` lives in baggage → every descendant span auto-tagged.
- Prompts migrated to LangFuse: start with `dd/planner/nodes/chapter_propose/prompts.py` (highest churn), then `dd/synth/nodes/sawc/prompts.py`. Label `production` is default; canary tests via label routing.
- Existing grader-dim scores → also written to LangFuse `scores` (keep the OTel histogram too; they target different consumers — Mimir for aggregates, LangFuse for per-trace inspection).
- Gold dataset: one small reference book + its 5 expected chapter titles + a faithfulness rubric. Re-run on every prompt-label promotion. Judge = NIM Llama 3.3 70B via rotator.
- Human-in-loop: planner `chapter_assign` items with confidence < 0.6 → LangFuse annotation queue.

### 5.2 YCS — YouTube Channel Summarizer (async ingestion + Adaptive Graph-RAG)

**Demonstrates:** `db.*` semconv, sessions/users, RAGAS-style evals, sampling tradeoffs.

- Sessions = one Ask conversation (browser-tab scope). `user_id` = channel_id, enabling per-channel cost ledger.
- Custom spans: `qdrant.hybrid_search`, `neo4j.entity_lookup`, `es.metadata_query`, `reranker.flashrank` — full `db.*` + `gen_ai.*` semconv blend.
- Transcript source as baggage attr (`ycs.transcript_source = yt-dlp | playwright`) → cohort comparison in Grafana (success rate by source).
- RAGAS evals (faithfulness, answer_relevance, context_precision) on a 50-pair golden set via rotator-as-judge.

### 5.3 RR — Research Radar (DeepAgents + FastMCP)

**Demonstrates:** `gen_ai.agent.*`, `gen_ai.tool.*`, subagent traces, MCP tool tracing.

- Wire `langfuse.callback.CallbackHandler` into the DeepAgents executor — one config call, full subagent tree appears in LangFuse with parent/child relationships.
- Augment `apps/fastmcp/middleware/telemetry.py` to emit **both** `mcp.tool.*` (current) AND `gen_ai.tool.*` (semconv dual-naming). Minimal diff, max compatibility.
- Sessions = one digest cycle (`digest_id`); tags = source list (`arxiv,s2,hf_daily,hn`).
- Evals: **novelty** (Jaccard of arxiv_ids vs last 7 digests), **implementability score** (LLM judge), **relevance-to-roadmap** (LLM judge over a small roadmap rubric file).

---

## 6. Proposed code organization

Two orthogonal axes decide where any observability file lives:

1. **`infra/` is vendor-split; `domains/` is signal-split.** Different SDKs (OTel vs LangFuse SDK) belong in different infra folders. Inside domains, files split by signal type (`spans` / `metrics` / `scores`) — because most signals flow through both vendors at once (one OTel emit lands in Tempo and LangFuse simultaneously). Vendor folders inside domains would force arbitrary categorization.
2. **Metrics: registry central, recorders co-located.** Instrument *definitions* (names, units, label vocabulary) live in `infra/otel/metrics_registry.py` so naming doesn't drift across features. `record_*` functions live next to the code that emits them. This deletes the current `apps/fastapi/domains/llm/rotator/otel_metrics/` misnomer — every recorder there is DD-side and will move to the relevant `dd/*/runtime/observability/metrics.py`.

Per-domain layout is a uniform three-file shape (`spans.py` + `metrics.py` + `scores.py`), plus the existing `domain.py` / `params.py` split from `docs/CODE-CONVENTIONS.md` where useful.

### Tree

```
apps/fastapi/infra/
  otel/                          # VENDOR FOLDER — transport only, no domain or LangFuse business concepts
    service.py                   # init_otel() bootstrap (current shape OK)
    exporters.py                 # Alloy gRPC + LangFuse OTLP + Mimir
    resource.py                  # build_resource() w/ git SHA + helm chart version
    semconv.py                   # gen_ai.* + db.* + mcp.tool.* constants + attr builders
    baggage.py                   # BaggageSpanProcessor + named contexts
    sampling.py                  # ParentBased + TraceIDRatio + AlwaysOn-on-error
    instrument.py                # auto-instrument httpx/redis/celery/fastapi/logging
    metrics_registry.py          # central INSTRUMENTS list (names, units, label vocab)
    metrics.py                   # _ensure_instruments() factory + get_instrument(name)
    filters.py                   # rate-limit collector-down startup noise
  langfuse/                      # VENDOR FOLDER — SDK-only features OTel doesn't cover
    client.py                    # lazy singleton, env-driven, BYOK key resolution
    prompts.py                   # get_prompt(name, label, vars) → cached, retry, version-pinned
    sessions.py                  # session context manager per feature
    scores.py                    # record_score(trace_id, name, value, comment)
    datasets/
      uploader.py                # one-shot push from a fixtures dir
      runner.py                  # eval-run orchestrator
    evals/
      judges/                    # one file per judge; all route through rotator (free-tier)
        faithfulness.py
        citation_accuracy.py
        ragas_relevance.py
        novelty.py
        implementability.py

apps/fastapi/domains/
  llm/rotator/observability/     # SIGNAL FOLDERS — uniform shape per domain
    spans.py                     # gen_ai.* span enrichment around LiteLLM calls
    metrics.py                   # rotator-specific: arm selection, retry, latency, bandit σ²
    scores.py                    # rotator-side scores (slot reserved; none today)
    domain.py                    # SpanAttrs frozen-dataclass groupings
    params.py                    # RECORD_CONTENT, MAX_PROMPT_BYTES…
  dd/synth/runtime/observability/
    spans.py                     # synth gen_ai.* spans
    metrics.py                   # record_chapter_outcome / record_bucket_split_overflow / record_classical_patch
                                 #   (moved from llm/rotator/otel_metrics/)
    scores.py                    # write_grader_dims_to_langfuse(trace_id, dims)
  dd/planner/runtime/observability/
    spans.py
    metrics.py
    scores.py
  ycs/runtime/observability/
    spans.py                     # db.* spans for Qdrant / Neo4j / ES / reranker
    metrics.py
    scores.py
  rr/runtime/observability/
    spans.py                     # gen_ai.agent.* + gen_ai.tool.*
    metrics.py
    scores.py

apps/fastmcp/
  middleware/
    telemetry.py                 # extend: emit both mcp.tool.* (current) AND gen_ai.tool.*
  infra/otel/                    # MCP-process mirror of FastAPI's OTel skeleton

observability/fixtures/          # gold datasets, mirror the domain split
  dd/reference_book/
  ycs/ask_qa_pairs.jsonl
  rr/known_good_arxiv.jsonl
```

### Organizational principles (priority order)

1. **`infra/` is vendor-split, `domains/` is signal-split.** A `spans.py` inside a domain emits via OTel and lands in both Tempo and LangFuse — putting it inside a `langfuse/` or `otel/` subfolder forces arbitrary categorization. Signal type (spans / metrics / scores) is the clean axis at the domain level. Vendor folders only at `infra/`.
2. **Metrics registry is central, recorders are co-located with callers.** `infra/otel/metrics_registry.py` is the single source of truth for instrument names, units, and label vocabulary. Each domain's `metrics.py` imports from it and owns its `record_*` functions next to the code that calls them. The current `otel_metrics/` folder under the rotator is deleted as part of this.
3. **`infra/otel/` owns transport.** No domain concepts, no LangFuse business concepts. SDK init, exporters, resource, sampling, baggage, semconv constants, metric registry.
4. **`infra/langfuse/` owns SDK-only features.** Prompt management, sessions, scores helper, datasets, evals. No knowledge of specific domains.
5. **`domains/<feature>/runtime/observability/` owns domain enrichment** — what a "video" / "chapter" / "subagent" is. Uniform three-file shape (`spans.py + metrics.py + scores.py`) across every feature so the layout is predictable.
6. **One file per judge** under `infra/langfuse/evals/judges/` — individually unit-testable, trivially swappable.
7. **Fixtures mirror the domain split** under `observability/fixtures/{dd,ycs,rr}/`.

---

## 7. Implementation order — max learning per hour, lowest blast radius first

| # | Step | Effort | Why early |
|---|---|---|---|
| 1 | Flip switches: `LITELLM_OTEL_V2=true`, `OTEL_SEMCONV_STABILITY_OPT_IN=gen_ai_latest_experimental`, add `langfuse_otel` to LiteLLM callbacks | ~30 min | Validates the existing OTLP pipe carries semconv attrs end-to-end |
| 2 | `infra/otel/baggage.py` — `BaggageSpanProcessor` + named contexts (`study_id`, `channel_id`, `digest_id`) | ~1 h | Unblocks every downstream span enrichment |
| 3 | `infra/langfuse/{client,prompts,sessions,scores}.py` skeleton | ~2 h | Foundation for features 4-7 |
| 4 | Wire `langfuse.callback.CallbackHandler` into RR DeepAgents executor | ~30 min | Biggest immediate visual payoff (full agent tree in LangFuse) |
| 5 | Migrate `dd/planner/nodes/chapter_propose` prompt → LangFuse prompt management; adopt label-based deploy | ~1 h | First "real" use of prompt management on the highest-churn prompt |
| 6 | Grader scores → LangFuse `scores` dual-write | ~1 h | First "real" use of scores; per-trace quality visible immediately |
| 7 | First gold dataset + faithfulness LLM-judge evaluator (DD reference book) | ~3 h | Closes the regression loop end-to-end |
| 8 | YCS custom `db.*` spans for Qdrant + Neo4j + ES + reranker | ~2 h | Cleanest `db.*` semconv showcase |
| 9 | Grafana dashboards: RED per provider, cost per tenant, p95 latency exemplar-linked to Tempo | ~2 h | Demo-quality dashboards built on existing `kd_*` + `gen_ai.*` metrics |
| 10 | Tail sampling at Alloy collector config | ~1 h | **Last** — needs real traffic to set thresholds correctly |

Total: ~14 hours for full coverage of every learning vector listed above.

---

## 8. Hard constraints kept in mind

- **Free-tier only** — judges run through the rotator (NIM-hosted Llama / Qwen), never paid SaaS. See [[feedback_free_tier_only]].
- **No in-cluster inference** — rerankers (FlashRank) and BPE tokenizers stay CPU-side; nothing GPU. The `kd_*` metrics already respect this.
- **BYOK rotator** — provider keys via the LLM credentials store at `domains/llm/credentials/`. LangFuse public/secret keys handled identically (Fernet at-rest in MinIO, KEK from env or auto-gen).
- **Self-hosted LangFuse** — one more K8s service in COELHO Cloud (OSS, free). Provision under `~/COELHOCloud/infrastructure/modules/langfuse/` following the existing Terragrunt pattern.
- **No deep-research harness usage** per [[feedback_no_deep_research_for_design]] — this doc was built from 4 targeted WebSearches + direct repo reads.

---

## 9. Open questions (resolve before implementation)

1. **LangFuse Helm chart vs Docker Compose** — homelab Helm is preferred for parity with the rest of the cluster. Confirm chart version compatibility with the v3.22+ baseline before locking.
2. **Where to host the prompt-label promotion gate** — manual flip in LangFuse UI vs CI gate via dataset eval threshold. Likely start manual, gate via CI once the first eval is green.
3. **PII / prompt content masking** — should `gen_ai.input.messages` and `gen_ai.output.messages` be captured for DD (likely yes — no PII) but masked for YCS (channel may contain user-comment quotes)? Codify in `infra/otel/semconv.py` as a `RECORD_CONTENT` per-feature flag.
4. **Sampling thresholds** — start at 100% in homelab, lower only if Tempo storage becomes a concern. Tail sampling at Alloy is the cleaner knob.

---

## 10. Sources (June 2026 SOTA references)

- [LangFuse — OpenTelemetry (OTEL) for LLM Observability](https://langfuse.com/integrations/native/opentelemetry)
- [OpenTelemetry — Semantic conventions for generative client AI spans](https://opentelemetry.io/docs/specs/semconv/gen-ai/gen-ai-spans/)
- [OpenTelemetry blog — Inside the LLM Call: GenAI Observability with OpenTelemetry (2026)](https://opentelemetry.io/blog/2026/genai-observability/)
- [OpenTelemetry — GenAI agent and framework spans semconv](https://opentelemetry.io/docs/specs/semconv/gen-ai/gen-ai-agent-spans/)
- [LiteLLM — LangFuse OpenTelemetry Integration](https://docs.litellm.ai/docs/observability/langfuse_otel_integration)
- [LangFuse — LangChain DeepAgents observability](https://langfuse.com/integrations/frameworks/langchain-deepagents)
- [LangFuse — Open Source Observability for LiteLLM Proxy](https://langfuse.com/integrations/gateways/litellm)
- [Greptime — How OpenTelemetry Traces LLM Calls, Agent Reasoning, and MCP Tools](https://greptime.com/blogs/2026-05-09-opentelemetry-genai-semantic-conventions)
- [OpenObserve — OpenTelemetry for LLMs: Complete SRE Guide for 2026](https://openobserve.ai/blog/opentelemetry-for-llms/)
- [LaunchDarkly — OpenTelemetry for LLM Applications: Practical Guide with LangFuse](https://launchdarkly.com/docs/tutorials/otel-llm-practical-guide-with-langfuse)
