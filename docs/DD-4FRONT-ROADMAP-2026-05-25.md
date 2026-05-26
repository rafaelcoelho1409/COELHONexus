# Docs Distiller — 4-Front Roadmap (2026-05-25)

**Status:** All Bundles 1-4 + speed ships (#141-#146) landed on master commit `ad82c16`.
Bundle 4 (route_score gate) was shipped, regressed quality, and was surgically reverted.
This document captures the next 10-bundle roadmap synthesized from end-of-day empirical
evidence + a deep SOTA research pass.

**Owner context:** assistant should treat this doc as the source of truth for what
ships next after conversation compaction. Each bundle has explicit file paths, LOC
estimates, validation gates, and cross-bundle dependencies.

---

## 1. Empirical state at session end (2026-05-25 ~23:00)

### 1.1 Pipeline composition (current master)

**Planner** — 8 nodes:
`corpus_load → embed_corpus → off_topic → cluster → refine → label → reduce → plan_write`

**Synth** — 7 nodes per chapter (route_score reverted):
`outline_sdp → digest_construct → sawc_write → sawc_derive → checklist_eval → mgsr_replan → render_audit_write`
(mgsr_replan loops back to sawc_write on CoRefine; max_iter=5, plateau-halt at delta<0.03)

### 1.2 Speed ships landed today (commit `ad82c16` + uncommitted on top)

| # | Ship | Where | Effect |
|---|---|---|---|
| Bundle 1 | UI CoRefine loopback edge + iter badge + chip | `apps/fasthtml/static/js/dd/synth.js`, `stagegraph.js`, `state.js` | UI correctly shows sawc_write running during loop |
| Bundle 2 | sawc_derive node (Analogical+MPSC) + 3-tier audit | `apps/fastapi/domains/dd/synth/sawc_derive/`, `render/{service,types}.py` | AI-derived expansion of thin signature refs |
| Bundle 3 | Ship A/B/C/D/E alignment fixes (schema reorder, ident-overlap, CoCoA judge, derive re-explain) | `sawc/{types,service,node}.py`, `checklist/{cocoa,node}.py`, `sawc_derive/{service,node}.py` | Drift from 21% → 4.8% |
| #141 | digest_construct concurrency 6→16 | `synth/digest/node.py:124` | More parallel LLM calls |
| #142 | MAMM-Refine N=3→N=2 drafts | `sawc/constants.py:34` | 33% fewer sawc LLM calls |
| #143 | CoCoA per-hash abstraction cache | `checklist/cocoa.py` | Caches stage-1 explainer specs by vault hash in MinIO |
| #144 | Token-counted embedding chunking + `truncate="END"` | `planner/embed_corpus/{constants,service}.py`, `llm/rotator/chain/service.py` | Embeds pack to ~7800 tok per chunk vs ~2000 prior |
| #145 | `transformers` → `tokenizers` (3.3MB Rust lib) | `apps/fastapi/pyproject.toml`, `embed_corpus/service.py` | 17× smaller install, byte-exact NIM parity |
| #146 | off_topic head+tail truncation (3K head + 1.5K tail) | `planner/off_topic/{constants,service}.py` | Catches DROP signals in both header AND footer |

### 1.3 Bundle 4 reverted (route_score gate)

Shipped a NIM-cross-encoder routing-quality gate (`route_score` node between
`digest_construct` and `sawc_write`). Threshold `logit ≥ 0.0` was mis-calibrated for
the actual NIM logit distribution — over-filtered ~95% of hashes. Result: ch-01 = 1/10
sections written, ch-02 = 0/10, ch-03 = 0/16, total ~30KB book vs prior ~210KB.

**Reverted** via the 4 modified files (`graph.py`, `sawc/node.py`, `state.py`,
`state.js`) returning to commit `ad82c16` state. `apps/fastapi/domains/dd/synth/route_score/`
directory deleted.

**Lesson:** any future routing-gate threshold must be calibrated from a real logit
distribution sample BEFORE shipping. The module structure was sound; only the threshold
was wrong. Re-shippable as a future bundle if calibrated against actual `(heading, code)`
pairs first.

### 1.4 Latest empirical FastMCP study run (post-Bundle-3, post-speed-ships)

**Planner output:**
- 335 docs → off_topic kept 331/335 (head+tail catch)
- cluster: 14 clusters, 74 noise, 156 boundary docs
- refine: 60% GMM fast-path, 40% LLM-judge; 76 reassigned, 16 sent to noise
- reduce: 8 chapters from 13 clusters
- plan_write: 315 sources assigned, **16 unassigned** ← **CRITICAL FAILURE MODE**
- Total planner wall: ~6 min

**The 16 unassigned docs are NOT noise — they're core teaching content:**
```
lifespans.md, composing-servers.md, dependency-injection.md, opentelemetry.md,
user-elicitation.md, calling-tools.md, mcp-proxy-provider.md, notifications.md,
client-roots.md, llm-sampling.md, sampling.md, init.md, lifespan.md,
docstring-parsing.md, version-check.md, versions.md
```
These were marked as HDBSCAN boundary docs (max_prob<0.5), GMM softmax+LLM-judge sent
them to noise, plan_write dropped them entirely. Multiple of these were the EXACT
prerequisite content that ch-02/ch-03 chapters needed (e.g., `opentelemetry.md` is the
sole source of `server_span`/`client_span`/`inject_trace_context` examples).

**Per-chapter synth output (8 chapters):**

| Ch | Title | Sources | H2 | H3 | src/H2 | Drift % | Wall |
|---|---|---|---|---|---|---|---|
| ch-01 | Framework Installation and CLI Tools | 42 | 9 | 45 | 4.7 | **2.5%** | ~10 min |
| ch-02 | Core Server Primitives | 48 | 20 | 105 | 2.4 | **12.2%** | ~22 min |
| ch-03 | Middleware and Error Handling | 23 | 21 | 101 | 1.1 | **14.9%** | ~22 min |
| ch-04 | API Design and OpenAPI Integration | 17 | 14 | 65 | 1.2 | **0.0%** ⭐ | ~14 min |
| ch-05 | Authentication and Transport Protocols | 85 | 40 | 210 | 2.1 | **4.1%** | ~24 min |
| ch-06 | Background Tasks and Server Transforms | 50 | — | — | — | (in-flight at session end) | — |
| ch-07 | Interactive UI and Prefab Components | 24 | — | — | — | (audit passed at 76924 B) | ~12 min |
| ch-08 | AI Platforms and Skills Extensions | 26 | — | — | — | (not yet started) | — |

**Critical empirical insight:** ch-04 with src/H2 = 1.2 has ZERO drift while ch-03 with
src/H2 = 1.1 has 14.9% drift. **Conclusion: src/H2 ratio is NOT the dominant predictor.**
The real predictor is **topical alignment between H2 outline topics and source pool
content.** ch-04 is tightly focused on OpenAPI (every H2 finds matching code in its 17
sources). ch-03 wanted to write about `server_span`/`client_span`/`inject_trace_context`
but `opentelemetry.md` was in the 16-unassigned list → writer drifted.

### 1.5 Chapter ordering — NOT pedagogically sequenced

Current chapter order = HDBSCAN cluster ID, essentially arbitrary. FastMCP empirical:
- ch-05 (Auth+Transport, 85 sources) comes AFTER ch-03 (Middleware), but Transport
  Protocols is FOUNDATIONAL — middleware depends on understanding transports.
- ch-01 (Installation) correctly first by coincidence; ch-08 (AI Platforms) correctly
  last by coincidence.

The `reduce` step in `planner/reduce/` produces chapter ordering but doesn't optimize
for prerequisite dependencies — just outputs in whatever LLM order.

### 1.6 Streaming chapter delivery — NOT IMPLEMENTED

`api/v1/dd/synth.py:_run_study_orchestrator` uses:
```python
results = await asyncio.gather(
    *[_run_one(i + 1, cid) for i, cid in enumerate(chapter_ids)],
    return_exceptions=True,
)
sem = asyncio.Semaphore(_STUDY_SEM)  # _STUDY_SEM = 1
```

**Problem:** gather collects ALL results at the END after every task completes. With
Semaphore(1), only one chapter runs at a time BUT the FIFO order is non-deterministic
(observed today: chapters processed in order 1, 3, 4, 5, 7, 2, 6, 8 — random).

**User UX impact:** must wait ~2h before any chapter is readable. Even though each
chapter writes atomically to MinIO at render_audit_write completion (~10-24 min in),
the UI doesn't surface that — user sees nothing until study orchestrator emits
`final_status=done`.

### 1.7 Synth speed — current bottlenecks

Per-chapter time budget (post-today's-speed-ships):
- `outline_sdp`: 1-5 min (single LLM call + repair)
- `digest_construct`: 4-7 min (per-source LLM, N=16 concurrent)
- `sawc_write`: 5-15 min per iter, 1-2 iters → 5-30 min total
- `sawc_derive`: ~5s (fast path; rarely promotes anything on FastMCP)
- `checklist_eval`: 1-4 min (CoCoA explainer+judge + atomic claim grounding + 5 single-shot)
- `mgsr_replan`: 5-30s
- `render_audit_write`: 5-30s

Total per chapter: 10-24 min. Book total: ~2 hours for 8 chapters sequential.

---

## 2. The 4 fronts (research-validated diagnoses)

### Front 1 — Clustering hyperparameters + 16-unassigned-docs

**Three concrete misconfigs in current `cluster/constants.py`:**
1. `_UMAP_N_NEIGHBORS = 15` — over-emphasizes local structure at 335-doc scale.
   BERTopic_Teen empirical and BERTopic discussion #600 recommend `n_neighbors=30` for
   small corpora (<1K docs) to bias toward global topical structure.
2. `_UMAP_DIM = 10` — too many dimensions for HDBSCAN density at this scale.
   Official BERTopic best practice: `n_components=5`.
3. **No `cluster_selection_epsilon` set** — HDBSCAN splits density-similar siblings.
   For L2-normalized cosine embeddings, ε=0.2-0.3 merges fragments without collapsing
   meaningful clusters.

**The 16-unassigned bug source:** `plan_write/node.py` lines 181-189 compute
`unassigned_keys = set(cluster_keys) - {assigned}`. The GMM softmax + LLM-judge in
refine sends ~16 docs to noise; plan_write silently DROPS them. **There is no
`reduce_outliers` rescue layer.**

**SOTA fix:** BERTopic's `reduce_outliers(strategy="c-tf-idf")` pattern. For every
doc still labeled noise OR `max_prob<0.5` after refine, compute its c-TF-IDF vector
against final cluster representations and assign to best cosine match if ≥0.10. The
c-TF-IDF cluster reps are ALREADY computed in `label/` step — just need to vectorize
unassigned docs against the same vocabulary.

### Front 2 — Pedagogical chapter ordering

**Current `reduce/node.py`:** USC-vote LLM produces K chapters from M clusters but
doesn't reason about prerequisite ordering. Output order = whatever the LLM happens
to emit, essentially arbitrary.

**SOTA fix:** insert a new `order_chapters` node between `reduce` and `plan_write`.
LLM-as-orderer with USC vote (N=3 samples → Borda aggregate). One prompt per sample
covering all K chapter titles + 2-line summaries; returns ordered chapter_id list.
Cost: ~3 LLM calls per study, free-tier-friendly.

Add deterministic safety: pin chapters whose label matches
`install|setup|getting started|cli|quickstart` to position 0.

References: arXiv 2507.18479 (LLM prerequisite prediction), arXiv 2501.12300
(LLM-assisted KG curriculum modeling, EDUCON 2025), arXiv 2511.17041 (CLLMRec
cognitive concept recommendation).

### Front 3 — Synth speed

**Three quick wins (today's research):**

1. **CoRefine iter-1 short-circuit** (`mgsr/node.py:_route_after_mgsr`):
   - `score >= 0.9` on iter 1 → HALT success, skip iter 2
   - `score < 0.5` on iter 1 → HALT, ship OP-12 best-seen (no realistic recovery)
   - Only loop when `0.5 <= score < 0.8`
   Effect: 30-40% chapters skip iter 2 (saves 5-15 min each)

2. **Batched multi-criterion judge** (`checklist/node.py`):
   Currently 5 separate LLM calls for 5 LLM-judge criteria. Combine into ONE call with
   structured JSON output. arXiv 2604.03684 ("Researchers waste 80% of LLM annotation
   costs") + arXiv 2301.08721 (batch prompting) → 4-5× judge cost reduction.
   Randomize criterion order across runs to mitigate position bias.

3. **Cascaded Selective Evaluation** (Trust-or-Escalate, ICLR 2025
   arXiv 2406.04449): cheap judge → escalate only on low confidence. Cerebras
   llama-3.3-70b as cheap arm, qwen3-235b/gpt-oss-120b as expensive. Empirical:
   80% human agreement covering 79% of cases with cheap judge.

### Front 4 — Streaming chapter delivery

**Current anti-pattern in `api/v1/dd/synth.py:749`:**
```python
results = await asyncio.gather(
    *[_run_one(i + 1, cid) for i, cid in enumerate(chapter_ids)],
    return_exceptions=True,
)
```

**SOTA replacement:**
```python
for i, cid in enumerate(ordered_chapter_ids):
    await _run_one(i + 1, cid)
    await emit_progress(study_thread_id, "study", "chapter_ready",
                        chapter_id=cid, position=i+1, n_total=n_total,
                        render_path=f"synth/{slug}/{cid}/README.md")
```

Strict-order + per-chapter SSE notification. The MinIO atomic write at
render_audit_write completion already provides the "now available" timestamp; we just
need the notification edge to flow through to the FastHTML UI.

FastHTML side: HTMX `hx-sse-connect` extension on the study page; render chapter card
the moment `chapter_ready` event arrives. Existing chapter strip in
`apps/fasthtml/static/js/dd/synth.js` has the cell-update primitives — just wire to
the new event name.

References: LangGraph SSE Streaming Guide (DeepWiki/fullstack-langgraph-python),
HTMX SSE extension docs, MinIO bucket notifications (deferred to bundle 4.3).

---

## 3. The 10-bundle ordered roadmap

Each bundle is independent enough to ship alone but compounds with prior bundles.
**Skaffold redeploy + 1 empirical FastMCP run is the validation gate after each.**

### Bundle 5 — Planner cluster fixes (PRIORITY 1, ~2 hours)

**Highest single-bundle quality lift in the entire roadmap. Unblocks Front 2.**

**5a — UMAP/HDBSCAN retune (~5 LOC, ~30 min)**

File: `apps/fastapi/domains/dd/planner/cluster/constants.py`

```python
# OLD:
_UMAP_DIM            = 10
_UMAP_N_NEIGHBORS    = 15
# Implicit: no cluster_selection_epsilon set

# NEW:
_UMAP_DIM                  = 5           # was 10
_UMAP_N_NEIGHBORS          = 30          # was 15
_CLUSTER_SELECTION_EPSILON = 0.2         # NEW

_CACHE_VERSION = "v4-2026-05-25"         # bump from v3 — invalidates stored .npz
```

File: `apps/fastapi/domains/dd/planner/cluster/node.py`

Wire `cluster_selection_epsilon=_CLUSTER_SELECTION_EPSILON` into the HDBSCAN call.
Pin `random_state=42` on UMAP if not already (reproducibility across study runs).

**Validation:** noise rate drops from ~22% to <10%. Cluster count moves closer to
target_K=8 (less reduce-LLM workload).

**5b — c-TF-IDF rescue layer (~50-70 LOC, ~1.5 h)**

File: `apps/fastapi/domains/dd/planner/refine/node.py`

After the existing GMM+LLM-judge passes complete, add a final rescue step:
```python
async def _rescue_noise_via_ctfidf(
    noise_keys: list[str],
    final_assignments: dict[str, int],
    cluster_reps: list[dict],
    cluster_keys_index: dict[str, str],   # key → body
    threshold: float = 0.10,
) -> dict[str, int]:
    """For every doc still labeled noise or with max_prob < 0.5 after
    GMM + LLM-judge, compute its c-TF-IDF vector against final cluster
    representations and assign to the best cosine match if ≥ threshold.

    Mirrors BERTopic's reduce_outliers(strategy='c-tf-idf'). Eliminates
    the 16-unassigned-docs problem at the planner stage.
    """
    # c-TF-IDF cluster reps come from the label/ step (already cached in MinIO).
    # Vectorize each noise doc in the same vocabulary, cosine-rank, assign.
    # Leave docs below threshold in a "miscellaneous appendix" bucket (small).
```

Wire into `refine/node.py` AFTER both GMM and LLM-judge finish, but BEFORE the
assignments are written to MinIO. Update `plan_write/node.py` to also handle a
`rescued_by_ctfidf` counter alongside `unassigned`.

**Validation gate:** re-run FastMCP planner; `unassigned_keys` drops from 16 → 0-3;
ch-02 and ch-03 contain `lifespans.md`, `opentelemetry.md`, etc. in their per-chapter
source lists. Re-run synth; ch-03 drift drops from 14.9% → <5%.

### Bundle 6 — Streaming chapter delivery (PRIORITY 2, ~3 hours)

**Biggest UX win in the entire roadmap. TTFR: ~2h → ~10-15 min.**

**6a — Strict-order orchestrator (~30 LOC)**

File: `apps/fastapi/api/v1/dd/synth.py:_run_study_orchestrator`

```python
# OLD (line 749):
results = await asyncio.gather(
    *[_run_one(i + 1, cid) for i, cid in enumerate(chapter_ids)],
    return_exceptions=True,
)

# NEW:
for i, cid in enumerate(chapter_ids):
    try:
        await _run_one(i + 1, cid)
    except Exception as e:
        logger.error(...)
        counters["failed"] += 1
        # continue to next chapter; don't break on one failure
```

Remove the `Semaphore(1)` since strict-order obviates it. Keep `return_exceptions=True`
semantics via the try/except.

**6b — Per-chapter SSE event (~30 LOC)**

After each `_run_one` returns successfully:
```python
render_key = f"synth/{slug}/{cid}/README.md"
await emit_progress(
    study_thread_id, "study", "chapter_ready",
    chapter_id=cid,
    position=i + 1,
    n_total=n_total,
    render_path=render_key,
    challenges_path=f"synth/{slug}/{cid}/challenges.md",
    flashcards_path=f"synth/{slug}/{cid}/flashcards.json",
)
```

**6c — FastHTML UI subscribe (~50 LOC + small CSS)**

File: `apps/fasthtml/static/js/dd/synth.js`

In the SSE message handler (around `pollSynthState`):
```js
if (ev.step === 'study' && ev.kind === 'chapter_ready') {
    const cid = ev.chapter_id;
    _markChStripCell(cid, 'done');  // existing primitive
    // Optionally surface a toast: "Chapter {cid} ready — click to read"
}
```

The chapter strip cell becomes clickable; click navigates to the Study page for that
chapter. Existing `_onStripCellClick` handler already does this for the post-study
case.

**Validation gate:** trigger study run, confirm ch-01 cell turns "done" + readable
~10-15 min after Start Synth (not 2h).

### Bundle 7 — CoRefine iter-1 short-circuit (PRIORITY 3, ~30 min)

File: `apps/fastapi/domains/dd/synth/mgsr/node.py` (or `graph.py:_route_after_mgsr`)

```python
def _route_after_mgsr(state: SynthState) -> str:
    stats = state.get("checklist_stats") or {}
    score = float(stats.get("pass_rate", 0.0) or 0.0)
    refine_iter = int(state.get("refine_iter", 0) or 0)
    prev = state.get("prev_checklist_score")
    prev_score = float(prev) if isinstance(prev, (int, float)) else -1.0

    # NEW: aggressive short-circuit on iter 1
    if refine_iter <= 1 and score >= 0.9:
        return "render_audit_write"     # high-confidence pass, skip iter 2
    if refine_iter <= 1 and score < 0.5:
        return "render_audit_write"     # no realistic recovery, OP-12 rescues

    # existing logic for iter >= 2 + plateau + budget
    if score >= _CHECKLIST_THRESHOLD: return "render_audit_write"
    if refine_iter >= _MAX_REFINE_ITER: return "render_audit_write"
    if refine_iter >= 2 and abs(score - prev_score) < _PLATEAU_DELTA:
        return "render_audit_write"
    return "sawc_write"
```

**Validation gate:** 30-40% of chapters skip iter 2 entirely. Each such chapter saves
5-15 min of wall time. Compounds with Bundle 6 — earlier completion = earlier SSE.

### Bundle 8 — Pedagogical chapter ordering (PRIORITY 4, ~2 hours)

**BLOCKED until Bundle 5 lands.** Can't order garbage chapters intelligently.

**8a — New `order_chapters` node (~80 LOC)**

Create `apps/fastapi/domains/dd/planner/order_chapters/`:
- `constants.py` — prompt version, USC sample count N=3, foundational keywords list
- `service.py` — Borda-aggregation function for ranked lists, deterministic prefix-rule
- `node.py` — LLM call with USC vote
- `__init__.py`

Wire into `apps/fastapi/domains/dd/planner/graph.py` between `reduce` and `plan_write`:
```python
NODE_ORDER = (
    "corpus_load", "embed_corpus", "off_topic",
    "cluster", "refine", "label", "reduce",
    "order_chapters",   # NEW
    "plan_write",
)
```

**8b — Persist `chapter_order` field**

`plan_write/node.py` reads the order from `order_chapters_path` and uses it for the
final `chapters` list (instead of the raw reduce output order).

**8c — Study orchestrator iterates in order**

Already handled by Bundle 6 if 6a uses `chapter_ids` from the persisted plan (which
is now pedagogically-ordered).

**Validation gate:** ch-01 = Installation/CLI, ch-02 = Server Primitives, ch-03 =
Transport Protocols (was buried in ch-05 before), ch-04 = Middleware, ch-05 = Auth,
ch-06 = API/OpenAPI, ch-07 = Background Tasks, ch-08 = UI + AI Platforms (or split).

### Bundle 9 — Batched multi-criterion judge (PRIORITY 5, ~2.5 hours)

File: `apps/fastapi/domains/dd/synth/checklist/node.py:_run_llm_judge`

Currently issues 5 separate LLM calls for 5 criteria
(`chapter_reads_coherently`, `claims_grounded_in_sources`, `terminology_consistent`,
`prose_code_first_not_meta_framing`, `code_refs_introduced_in_prose`). Combine into
ONE prompt returning structured JSON:

```python
_BATCHED_JUDGE_PROMPT = """
You are evaluating a synthesized chapter. Return ONE JSON object with
5 boolean criteria + 5 one-sentence justifications.

CRITERIA (randomize order across runs to mitigate position bias):
{shuffled_criteria_block}

CHAPTER:
{chapter_md}

DIGEST GROUNDING:
{digest_grounding}

OUTPUT (strict JSON):
{
  "chapter_reads_coherently":            {"passed": <bool>, "feedback": "<one sentence>"},
  "claims_grounded_in_sources":          {"passed": <bool>, "feedback": "<one sentence>"},
  "terminology_consistent":              {"passed": <bool>, "feedback": "<one sentence>"},
  "prose_code_first_not_meta_framing":   {"passed": <bool>, "feedback": "<one sentence>"},
  "code_refs_introduced_in_prose":       {"passed": <bool>, "feedback": "<one sentence>"}
}
"""
```

Pydantic schema enforces the 5 keys. Pair with prompt-prefix caching where supported
(Cerebras has it free-tier; Gemini implicit caching auto-applies). Randomize criterion
ORDER (not key names) across runs to balance the position-bias effect.

**Validation gate:** `checklist_eval` total wall drops from ~1-4 min to ~0.3-1 min.
Criterion pass rates remain within ±5% of unbatched (statistical noise level).

### Bundle 10 — HDBSCAN-native `membership_vector` rescue (PRIORITY 6, ~1 hour)

After Bundle 5 proves the rescue pattern works, replace the GMM softmax boundary-resolver
in `refine/node.py` with HDBSCAN's own `membership_vector(clusterer, doc_emb)` →
manifold-aligned soft membership. Beats Gaussian-assumption GMM by 5-10pp accuracy on
density-based clusters.

Requires `prediction_data=True` flag at HDBSCAN fit time in `cluster/node.py`:
```python
clusterer = hdbscan.HDBSCAN(
    ...,
    prediction_data=True,  # NEW — adds ~10-20% memory at 335 docs
)
```

`refine/service.py`:
```python
import hdbscan
def hdbscan_native_membership(clusterer, doc_emb):
    """Returns N×K soft membership vector aligned with the cluster
    manifold. Replaces _gmm_softmax_sharpen for boundary resolution.
    """
    return hdbscan.prediction.membership_vector(clusterer, doc_emb)
```

**Validation gate:** boundary-doc reassignment accuracy improves by 5-10pp on the
gold-standard set (if we have one; otherwise compare ch-02/ch-03 drift before/after).

---

## 4. Deferred bundles (only after 5-10 land)

### Bundle 11 — Cascaded Selective Evaluation (Trust-or-Escalate)

ICLR 2025 pattern: cheap judge first, escalate to expensive only on low confidence.
~150 LOC, ~4h. Do AFTER Bundle 9 validates batched judge.

### Bundle 12 — Speculative parallel chapters

After Bundle 6 ships strict-order, optionally bump `_STUDY_SEM=1` to 2-3 for chapters
2-K only (ch-01 stays sequential for fastest TTFR). Only meaningful AFTER Synth→Celery
(Bundle 13) lands.

### Bundle 13 — Synth → Celery migration (#97)

Move SAWC's heavyweight bandit calls + `book_harmonize` + CoCoA off the FastAPI event
loop. ~1 day, mechanical port mirroring the Planner→Celery migration that already
landed. Unblocks real concurrent chapter generation.

### Bundle 14 — Pairwise ordering (Front 2 alternative)

Only if Bundle 8 USC-vote shows ordering instability across N samples.

### Bundle 15 — Route_score re-introduction (calibrated)

The Bundle 4 work was sound; the threshold was wrong. To re-ship:
1. Run NIM rerank on a sample of (heading, code) pairs offline.
2. Compute logit distribution → pick threshold at the 30th-50th percentile (not 0.0).
3. Re-introduce `route_score` node with calibrated threshold + KD_DISABLE_ROUTE_SCORE
   env flag default ON until empirically validated.

---

## 5. Cross-bundle dependency map

```
Bundle 5 (planner fixes)
    │
    ├── BLOCKS Bundle 8 (pedagogical ordering needs good chapters first)
    ├── AMPLIFIES every synth chapter (better source assignments → less drift)
    │
Bundle 6 (streaming)
    │
    ├── BLOCKS Bundle 12 (speculative parallel needs strict-order foundation)
    └── AMPLIFIED BY Bundle 7 (faster chapter completion = earlier SSE)
    │
Bundle 7 (iter-1 short-circuit)
    │
    └── AMPLIFIES Bundle 6 + reduces total book wall time
    │
Bundle 8 (chapter ordering)
    │
    ├── DEPENDS ON Bundle 5
    └── AMPLIFIES Bundle 6 (correct first chapter = better UX)
    │
Bundle 9 (batched judge)
    │
    └── Independent; pairs with Bundle 11 later
    │
Bundle 10 (HDBSCAN-native rescue)
    │
    └── REFINES Bundle 5's rescue mechanism
```

**Hard ordering rules:**
- Bundle 5 BEFORE Bundle 8 (chapters must contain right content before ordering them)
- Bundle 6 BEFORE Bundle 12 (strict-order foundation before parallel speculation)
- Bundle 13 (Celery migration) BEFORE Bundle 12 (no real parallelism without Celery)

**Optimal interleave:**
1. Bundle 5 (cluster fixes) ← UNBLOCKS quality
2. Bundle 6 (streaming) ← UX win, independent of 5's outputs
3. Bundle 7 (iter-1 short-circuit) ← compounds with 6
4. Bundle 8 (ordering) ← needs 5
5. Bundle 9 (batched judge) ← independent
6. Bundle 10 (HDBSCAN-native rescue) ← refines 5

---

## 6. Validation playbook (per bundle)

Each bundle ends with `kubectl logs ... | grep ...` checks for the specific signal
that confirms the ship landed correctly. Patterns to look for:

| Bundle | Confirmation signal |
|---|---|
| 5a | `cluster: ... groups, ... noise` — noise count < 35 on FastMCP 335-doc corpus |
| 5b | `plan_write: ... 0-3 unassigned` (down from 16); ch-03 drift < 5% |
| 6 | UI chapter strip cells turn "done" + clickable BEFORE study orchestrator's `done — N/N completed` event |
| 7 | `HALT success (pass_rate=0.9X ...)` at iter=1 OR `HALT plateau (iter=1, ...)` — no iter=2 logs for 30-40% of chapters |
| 8 | `chapter_order: ['ch-01-installation...', 'ch-02-server-primitives...', 'ch-03-transport...', ...]` in plan-latest.json |
| 9 | `[checklist_eval] ... judge_wall=<small>ms` (one call not five); `n_llm=5 n_passed=N` aggregation works |
| 10 | `[refine] membership_vector boundary docs ...` log replaces `[refine] GMM fast-path ...` |

---

## 7. Sources (deep research bibliography)

### Clustering
- [BERTopic Best Practices](https://maartengr.github.io/BERTopic/getting_started/best_practices/best_practices.html)
- [BERTopic Outlier Reduction](https://maartengr.github.io/BERTopic/getting_started/outlier_reduction/outlier_reduction.html)
- [HDBSCAN cluster_selection_epsilon](https://hdbscan.readthedocs.io/en/latest/how_to_use_epsilon.html)
- [HDBSCAN approximate_predict + membership_vector](https://hdbscan.readthedocs.io/en/latest/prediction_tutorial.html)
- [BERTopic UMAP/HDBSCAN Tuning Discussion #600](https://github.com/MaartenGr/BERTopic/discussions/600)
- [BERTopic_Teen empirical (n_neighbors=30)](https://pmc.ncbi.nlm.nih.gov/articles/PMC12378273/)

### Chapter ordering
- [How Well Do LLMs Predict Prerequisite Skills? (arxiv 2507.18479)](https://arxiv.org/html/2507.18479v1)
- [CLLMRec: LLM-powered Cognitive-Aware Concept Recommendation (arxiv 2511.17041)](https://arxiv.org/html/2511.17041)
- [LLM-Assisted KG Completion for Curriculum Modelling (EDUCON 2025, arxiv 2501.12300)](https://arxiv.org/html/2501.12300v1)
- [Automated Curriculum Analysis Using LLMs and Knowledge Graphs (Sage 2025)](https://journals.sagepub.com/doi/10.1177/17248035251360196)
- [Are Optimal Algorithms Still Optimal? (LLM Pairwise Ranking, arxiv 2505.24643)](https://arxiv.org/pdf/2505.24643)

### Synth speed
- [Trust or Escalate: LLM Judges with Provable Guarantees (ICLR 2025)](https://proceedings.iclr.cc/paper_files/paper/2025/file/08dabd5345b37fffcbe335bd578b15a0-Paper-Conference.pdf)
- [LLM-as-Judge Best Practices 2026 (FutureAGI)](https://futureagi.com/blog/llm-as-judge-best-practices-2026)
- [Tuning LLM Judge Design Decisions for 1/1000 of the Cost (arxiv 2501.17178)](https://arxiv.org/pdf/2501.17178)
- [Batch Prompting: Efficient Inference with LLM APIs (arxiv 2301.08721)](https://arxiv.org/pdf/2301.08721)
- [Researchers waste 80% of LLM annotation costs (arxiv 2604.03684)](https://arxiv.org/pdf/2604.03684)
- [Cerebras Prompt Caching](https://inference-docs.cerebras.ai/capabilities/prompt-caching)

### Streaming chapter delivery
- [LangGraph SSE Streaming (DeepWiki, fullstack-langgraph-python)](https://deepwiki.com/langchain-ai/langgraph-fullstack-python/2.3-sse-streaming)
- [Streaming AI Agent with FastAPI & LangGraph (2025-26 Guide)](https://dev.to/kasi_viswanath/streaming-ai-agent-with-fastapi-langgraph-2025-26-guide-1nkn)
- [SSE with FastAPI + React (LangGraph)](https://www.softgrade.org/sse-with-fastapi-react-langgraph/)
- [MinIO Bucket Notifications](https://min.io/docs/minio/linux/administration/monitoring/bucket-notifications.html)
- [Limiting concurrency in Python asyncio (imap_unordered)](https://death.andgravity.com/limit-concurrency)

---

## 8. Memory pointers to update after compaction

Index file: `/home/rafaelcoelho/.claude/projects/-home-rafaelcoelho-Workbench-COELHONexus/memory/MEMORY.md`

Add:
```
- [project_4front_roadmap_2026_05_25.md](project_4front_roadmap_2026_05_25.md) —
  10-bundle ordered roadmap for Planner clustering + chapter ordering + Synth speed +
  streaming chapter delivery. Empirical FastMCP run shows 16-unassigned-docs bug
  causes ch-02/ch-03 catastrophic drift. Bundle 5 (cluster fixes) is highest-ROI
  next ship. Source: docs/DD-4FRONT-ROADMAP-2026-05-25.md.
```

Pointer file: `/home/rafaelcoelho/.claude/projects/-home-rafaelcoelho-Workbench-COELHONexus/memory/project_4front_roadmap_2026_05_25.md`

```markdown
---
name: project-4front-roadmap-2026-05-25
description: 10-bundle ordered roadmap to fix planner clustering, add pedagogical
  chapter ordering, optimize synth speed, and ship streaming chapter delivery
metadata:
  type: project
---

10-bundle ordered roadmap synthesized 2026-05-25 from FastMCP empirical evidence +
SOTA deep research. **Highest-priority ship: Bundle 5** (UMAP/HDBSCAN retune +
c-TF-IDF rescue layer) fixes the 16-unassigned-docs bug that caused ch-02/ch-03
catastrophic drift (12-15% vs ch-01's 2.5%). Bundle 5 unblocks Bundle 8 (pedagogical
ordering). Bundle 6 (streaming chapter delivery) is the biggest UX win
(TTFR ~2h → ~10-15 min). Bundles 7+9+10 are speed/quality tightening.

**Bundles 1-4 + speed ships #141-#146 already landed** (Bundle 4 reverted due to
mis-calibrated route_score threshold; module structure was sound, re-shippable as
Bundle 15 after offline logit calibration).

Why: 16 unassigned docs (lifespans/opentelemetry/composing-servers/dependency-injection
etc.) are CORE teaching content lost between HDBSCAN noise classification and
plan_write. ch-03 wanted to write about `server_span` but `opentelemetry.md` was in
the unassigned 16 → writer drifted to unrelated code → 14.9% drift.

How to apply: see [[project_dd_pipeline_sota_comparison_2026_05_23]] for previous
SOTA passes. Full bundle-by-bundle plan with file paths, LOC estimates, validation
gates: docs/DD-4FRONT-ROADMAP-2026-05-25.md. Always validate each bundle with
1 FastMCP study run before proceeding to the next.
```
