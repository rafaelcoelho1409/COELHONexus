# Embedding Curator — design + build plan (2026-09-12)

**Status (2026-09-13):** rotator-side Embedding Curator shipped and tested against real infra. Nexus-side YCS integration also shipped (code complete, compiles + import-checked; live end-to-end testing still needs a skaffold redeploy — no Nexus pods running at time of writing).
**Ships in:** `~/Workbench/COELHOLLMRotator` (`domains/embeddings/`) + `~/Workbench/COELHONexus` (`domains/llm/embeddings/`, `domains/ycs/embeddings/`, and every YCS call site listed below).
**Triggered by:** a real production break in YCS's Qdrant ingestion, discovered while assessing a Raiam Santos McArn channel ingestion run.

---

## 2026-09-13 — Nexus-side integration shipped

All of §6's ship order below is done except the final live end-to-end test (blocked on a skaffold redeploy, not code). What changed, concretely:

- **New `domains/llm/embeddings/` module** (Nexus) — an independent embedding-endpoint connection, deliberately NOT sharing chat's pooled client (an earlier version of this wrongly reused chat's connection; corrected per explicit instruction — embeddings needed to be pointable at a different endpoint than chat, same flexibility chat's LLM Endpoint already has). Own Settings-page card (Base URL / API Key / Model), own credential (`COELHO_EMBEDDING_API_KEY`), own pooled `AsyncOpenAI` client, own endpoint-resolution TTL cache — same shape as `chain/service.py`'s chat adapter, independently configurable. No language-preference field exposed in the UI (YCS ingests multiple languages per run; the parameter still exists on `embed_probe_async`/`embed_texts_async` for internal/programmatic use only).
- **`domains/ycs/embeddings/service.py::NVIDIAEmbeddings` → `ExternalEmbeddings`.** Hardcoded NIM HTTP calls replaced with calls to `domains.llm.embeddings.embed_texts_async()`. Kept the LangChain `Embeddings` interface (sync `embed_documents`/`embed_query`, bridged to the real async calls via a dedicated thread pool for callers outside our control) but added native `aembed_documents`/`aembed_query` that our own call sites use directly, since they're already inside async functions. This one class swap fixed **four** call sites at once:
  1. Qdrant ingest (`ingestion/service.py::_flush`)
  2. Qdrant query-time embedding, Ask/RAG (`retriever/qdrant_hybrid.py::retrieve`) — found only during the full audit, wasn't on anyone's radar before
  3. Neo4j entity-dedup (`graph_builder/service.py::_embed_ids_for_resolution`) — previously a *second* `NVIDIAEmbeddings` instance pinned to `baai/bge-m3`; now shares the same singleton as everything else, since the endpoint resolves its own model dynamically and there's no client-side way to pin a different one per call anymore. `EMBED_COSINE_CUTOFF` (0.85) was tuned against bge-m3's specific score distribution — flagged as possibly needing re-tuning against whatever the endpoint currently resolves to, not silently assumed fine.
  4. (implicitly) anywhere else that reused the same singleton via `create_dense_embeddings()`.
- **Dimension learned at runtime, never hardcoded** — `get_embedding_info()` (replaces `get_embedding_dimensions()`) makes one real probe call (`embed_probe_async`) the first time a run needs it, caching `(dimensions, model)` on the singleton. No more static `MODEL_DIMENSIONS` table.
- **Model-identity guard added to `ingestion/service.py::ensure_collection`** — the existing dimension-mismatch auto-recreate guard only caught a dimension *change*; it silently passed a same-dimension-different-model switch, which would mix incomparable vectors in one cosine space. Now every point payload carries `embedding_model` (sampled the same way `content_hash` already is), and a stored-vs-current mismatch triggers the same drop+recreate path dims-mismatch already does.
- **Neo4j bandit-shim crash fixed** (separate from embeddings, but shipped in the same pass since it was already fully diagnosed and blocking Neo4j ingestion outright): `pick_ycs_neo4j_deployment_bandit`/`record_ycs_neo4j_reward`/`release_ycs_provider_slot` are now real `async def`s with the shapes `neo4j_task/task.py` actually calls; `build_ycs_neo4j_pinned_chain` got an explicit definition instead of falling through the zero-arg `__getattr__` stub. `resolve_entities` also had to become `async def` (its embedding call now genuinely awaits), with both call sites (`graph_builder/service.py`, `neo4j_task/task.py`) updated to `await` it.
- **Readiness gate removed, not fixed** — `neo4j_task/task.py` used to hard-fail if none of 6 old per-provider keys (NVIDIA/GROQ/CEREBRAS/MISTRAL/GOOGLE/DEEPSEEK) were set. That check no longer reflects how chat routing works (always goes through the LLM Endpoint, which resolves to a working default even unconfigured) — removed rather than patched, matching this session's established direction of not maintaining synthetic pre-flight checks that map to a deleted architecture (same reasoning as DD's `is_external_endpoint()` hardcoded-True finding).
- **Verified repo-wide via AST cross-check** — every `ImportFrom` targeting the 4 touched modules (`domains.ycs.embeddings`, `domains.llm.embeddings`, `domains.llm.rotator.chain`, `domains.ycs.graph_builder`) resolves to a real exported name. Zero mismatches.
- **Deferred, not resolved:** `api/v1/ycs/agents/byok/*` — confirmed orphaned (the live Ask graph never reads its persisted config), but it has a visible UI component on the Ask page (`static/js/ycs/ask.js`), making removal a more consequential, visible change than the backend-only fixes above. Left as-is rather than removed unilaterally.
- **Still needed:** a real end-to-end ingestion run once Nexus is redeployed, specifically to check YCS's real batch size (50 texts/call) against the rotator's current winner — every test so far, on both the rotator and Nexus sides, has been 1-3 texts at a time.

---

## 1. What broke, and why

Two independent root causes were found by reading the actual celery logs for run `extract_videos[d0c4b356]` (25 videos, 19 transcripts succeeded) — neither was CAPTCHA, despite that being the original suspicion.

### Fix 1 — dead embedding model (NOT YET APPLIED — do this first, independent of everything else)

```
POST https://integrate.api.nvidia.com/v1/embeddings "HTTP/1.1 410 Gone"
EmbeddingError: NIM embedding API HTTP 410: "The model 'nvidia/llama-nemotron-embed-1b-v2'
has reached its end of life on 2026-08-25T09:00:00Z and is no longer available."
```
`apps/fastapi/domains/ycs/embeddings/params.py:22-25` hardcodes this dead model as the default `EMBEDDING_MODEL`. Every `ingest_to_qdrant` Celery task has been failing outright since Aug 25 — unrelated to which channel or video is being ingested.

**Stopgap (5 min, ship immediately, does not depend on the rotator work below):** swap the default to `nvidia/llama-embed-nemotron-8b` — confirmed SOTA on the MMTEB benchmark (Oct 21 2025, #1 multilingual, 131 tasks, 250+ languages, purpose-built for cross-lingual retrieval) and already flagged as the target pick in an earlier DD-Synth SOTA note (2026-05-23). Add its entry to `MODEL_DIMENSIONS` (same file). Same base_url, no Qdrant schema break for new writes.

### Fix 2 — `ytcfg`/`INNERTUBE_CONTEXT` race (SHIPPED this session)

5/25 videos failed with `no INNERTUBE_CONTEXT` → fell through to the slow DOM-scrape path → burned ~100s of hydration/panel-wait budget → failed `Transcript button not found`, 3 retries, permanent loss. Other videos hit the *identical* failure and recovered on a later retry via `get_panel` once contention eased (45.5s/video worst case → 4.0s/video clear). This proved it was a **transient render-contention race, not a captcha or permanent block**: `_fetch_via_get_panel`/`_fetch_via_get_transcript` read `window.ytcfg.get('INNERTUBE_CONTEXT')` immediately after `domcontentloaded` with no readiness wait, unlike `_get_player_state`'s existing wait for `ytInitialPlayerResponse`.

**Fix applied:** `apps/fastapi/domains/ycs/transcript/service.py` — added `_wait_for_innertube_context(page, timeout_ms=3000)`, called right before Path 1/2 fire. Best-effort (times out silently, existing fallback chain still applies). Compiles clean, not yet redeployed.

---

## 2. The bigger question this surfaced

YCS's embeddings path (`domains/ycs/embeddings/service.py::NVIDIAEmbeddings`) hardcodes NIM directly with no fallback — the single point of failure that turned one vendor's model retirement into a full outage. DD's own embedding path (`domains/llm/rotator/chain/service.py::_get_embeddings()`) already avoids this by defaulting to a **local FastEmbed ONNX model**, only reaching NIM as a last resort — explicitly "to avoid NIM 403/EOL" per its own comment. That precedent, plus the fact the rotator already centralizes *chat* model routing for exactly this reason, is why the fix isn't "just pick a better NIM model" — it's "stop hardcoding any single vendor's embedding model in Nexus at all."

---

## 3. Architecture decision

**Build a new top-level domain in the rotator repo: `domains/embeddings/`** (sibling to `domains/llm/`, NOT nested under `domains/llm/rotator/`).

Why not `domains/llm/rotator/embeddings/`: it isn't a rotation strategy — no bandit, no live per-call switching (vectors from different models aren't comparable, so switching requires an explicit full re-embed, unlike chat where every call is independent). It also isn't strictly an LLM-generation concern at all — different API shape, no completions. Nesting it inside `rotator/` would misrepresent what it does.

Why a new top-level domain and not a new repo: the rotator already has the exact sibling infrastructure needed — `domains/llm/rotator/benchmarks/` (LLM benchmark scoring, TOPSIS+z-score) and `domains/llm/rotator/discovery/` (live catalog/availability) are the same two problems (rank by benchmark, verify liveness) applied to a different model type. A new repo would duplicate credential storage, deploy chart, dev loop, and auth for what is really "one more thing the gateway serves."

**Concept name:** "Embedding Curator" — deliberately not "picker" or "router," to avoid implying live rotation. No extra nesting under `domains/embeddings/` (no `curator/` subfolder) — unlike `rotator`, which names one strategy among conceivable future ones, embeddings only has this one serving strategy today.

**Key design rule (non-negotiable):** the curator advises, it never auto-swaps the live model. Any change in "current best pick" is surfaced as an advisory flag; applying it requires an explicit re-embed migration triggered separately.

**Language-aware selection:** the caller optionally supplies a ranked language hint (e.g. `languages=["pt","en"]`, default `["multilingual"]` when omitted) — benchmark rank is genuinely language-dependent (confirmed: a dedicated Brazilian-Portuguese benchmark, MTEB-BR, exists precisely because generic multilingual rank doesn't reflect PT-BR performance). Language filtering happens *before* the free-tier-availability intersection, not instead of it.

---

## 4. Research findings

### Benchmark source
- **MTEB leaderboard** (Hugging Face) is the canonical source. It moved to dataset/API-based access in 2026 (CSV export deprecated as of sentence-transformers 5.4.x, April 2026) — pull `mteb/results` (HF dataset, parquet) via `huggingface_hub`/`datasets`, filter `task_type=="Retrieval"` + language. Do not scrape the leaderboard Space UI (fragile, no contract).
- **MTEB-BR** (arXiv 2607.04581, published July 2026) is a dedicated 22-task Brazilian-Portuguese benchmark, 93 models evaluated (73 open-weight, 20 closed API) — the right source for PT-BR-specific ranking. Its full per-model retrieval scores weren't accessible via abstract-only fetch; **needs a follow-up direct pull of its HF Space/results table before finalizing the PT-BR ranking** — flagged as open, not resolved.

### Ranked candidates (free-tier providers already vetted into the rotator's registry only)

| Rank | Provider / Model | Basis |
|---|---|---|
| 1 | **NIM — `nvidia/llama-embed-nemotron-8b`** | MMTEB SOTA (Oct 2025), 250+ languages, cross-lingual by design |
| 2 | **Gemini — `gemini-embedding-001`** | Near-SOTA multilingual (68.32, prior #1), genuinely free (~10M TPM per Google's own ToS), different vendor from NIM — best EOL fallback |
| 3 | SambaNova — `E5-Mistral-7B-Instruct` | Confirmed real `/v1/embeddings` endpoint, decent multilingual baseline |
| 4 | Groq — `nomic-embed-text-v1_5` | Confirmed real endpoint, English-centric — weak PT-BR fit, fallback only |
| 5 | Cerebras — `cerebrasembeddings1` | Confirmed endpoint (768d, 100 RPM), no published benchmark — last-resort fallback |
| — | DeepSeek | No native embeddings endpoint (open GitHub feature request only) — excluded |
| — | Mistral `mistral-embed` | Free tier is explicitly "Experiment... not for production" — same rejected pattern as Cohere/Jina under the free-tier-only rule — excluded |
| — | OpenRouter | No embeddings support confirmed in this pass — needs direct verification, not yet excluded or included |

---

## 5. Implementation design

- **Refresh cadence:** on-demand + long TTL (mirror `chain/service.py::_apply_endpoint()`'s existing pattern, `_ENDPOINT_RESOLVE_TTL_S`-style), not Celery Beat — benchmark data moves on the order of weeks, and Beat needs a DB-backed scheduler for multi-worker safety that this scale doesn't warrant.
- **Liveness/EOL watchdog:** cheap probe of the cached pick when the TTL expires; on failure, cascade to the next-ranked registry candidate immediately, set an advisory flag. Never triggers a re-embed automatically.
- **API surface:** `POST /v1/embeddings` (OpenAI-compatible proxy; `dimensions` passed through only when the resolved model documents support for it — it's an OpenAI-specific feature most third parties don't implement) + `GET /embeddings/recommend?languages=pt,en` (advisory: current pick, score, alternates, liveness status).
- **Module layout** (mirrors `llm/rotator/benchmarks/` and `discovery/`):
  ```
  domains/embeddings/
    service.py   # provider calls + periodic benchmark/liveness refresh
    domain.py    # pure ranking/selection: pick_best(languages) -> RankedCandidates
    keys.py      # provider → embedding-model registry
    params.py    # refresh cadence, thresholds
    config.py
  ```

---

## 6. Ship order

0. **Stopgap (Nexus, do now, independent):** swap the dead default model in `embeddings/params.py`, add its `MODEL_DIMENSIONS` entry. Unblocks production immediately.
1. **Rotator:** scaffold `domains/embeddings/` (files above), `keys.py` registry from §4.
2. **Rotator:** benchmark ingestion — `huggingface_hub`/`datasets` dep, pull `mteb/results`, TTL cache. Resolve the MTEB-BR direct-access open question first.
3. **Rotator:** `domain.py::pick_best(languages)` — intersect benchmark rank with registry availability.
4. **Rotator:** liveness/EOL watchdog + cascade-on-failure logic.
5. **Rotator:** `/v1/embeddings` + `/embeddings/recommend` endpoints.
6. **Decision needed before step 7:** re-embed migration strategy — in-place re-embed vs collection versioning by model+dim. Not blocking initial ship (current pick stays until manually triggered), but must be decided before Nexus wiring points anywhere.
7. **Nexus:** rewire `domains/ycs/embeddings/service.py` to call the rotator's new endpoint instead of NIM directly (same shift chat completions already made).
8. **Validate:** redeploy both services, run a real YCS ingestion batch, confirm no more 410-class failures and the picked model matches expectations.
9. **Document:** log to `~/Workbench/COELHOLLMRotator/docs/rotator-roadmap.md` per the rotator's existing tracking convention.
