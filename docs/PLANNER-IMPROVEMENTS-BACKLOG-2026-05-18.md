# Planner improvements backlog — 2026-05-18

Captured from production runs of cluster → refine → label → reduce on pydantic
(85 docs) and langchain-langgraph-deepagents (744 docs).

## Observations driving these items

- **Pydantic outline is too thin** — 2 chapters from 3 clusters. Pydantic deserves
  6-10 chapters (Models, Validators, Types, Fields, Config, Errors, Serialization,
  etc.). Root cause: cluster's `_HDBSCAN_MIN_CLUSTER=8` is too aggressive for
  small corpora (~85 docs). With 85 / 8 = ~10 max clusters, we got 3.
  This cascades: 3 clusters → 3 labels → 2 chapters.
- **LangChain stack outline is theme-organized, not library-organized** — 5 chapters
  group by topic (Observability, Agent Dev, Integrations, Advanced, Releases).
  LangGraph + DeepAgents docs ARE present but folded into theme chapters:
  - LangGraph (cluster #10 "LangGraph API") → "Advanced Agent Topics"
  - DeepAgents (cluster #12 "Deep Agent Configuration") → "Agent Development Fundamentals"

  This is correct when content overlaps across libraries (Observability touches
  all three), but inappropriate when the user wants per-library guides
  ("LangGraph Guide", "DeepAgents Guide").

## Three improvement options (ranked by impact-to-effort)

### 1. Adaptive `min_cluster_size` in cluster node (small fix, big win)

**File:** `apps/fastapi/services/docs_distiller/planner/nodes/cluster.py`

**Change:** replace the hardcoded `_HDBSCAN_MIN_CLUSTER = 8` with an adaptive
calculation based on corpus size:

```python
min_cluster = max(3, n_docs // 15)
```

**Impact:** pydantic (85 docs) → ~5-7 min cluster size → ~12-17 clusters →
~6-10 chapter outline (matches pedagogical expectation). LangChain stack
(744 docs) stays at ~50 → unchanged behavior at scale.

**Effort:** 1-line change + adjust cache version to invalidate old blobs.

**Risk:** at very small N the clusters become single-doc "clusters" that
HDBSCAN treats as noise. Floor at 3 mitigates.

### 2. Library-aware reduce prompt (small fix, optional behavior)

**File:** `apps/fastapi/services/docs_distiller/planner/nodes/reduce.py`

**Change:** append a hint to `_build_reduce_prompt`:

> "If the clusters clearly belong to distinct sub-libraries (e.g. LangChain
> vs LangGraph vs DeepAgents based on label / keywords / member docs), PREFER
> keeping each sub-library's content in its own chapter. If content overlaps
> across libraries (e.g. tracing, observability, deployment) group by THEME
> instead."

**Impact:** lets the big-LLM decide topic-vs-library segregation per cluster
based on actual content. Should produce "LangGraph API and Patterns",
"DeepAgents Configuration", etc. as separate chapters when justified.

**Effort:** 3-5 line prompt addition + cache version bump.

**Risk:** for single-library frameworks (pydantic) the hint is inert. For
stacks (langchain-langgraph-deepagents) it should improve outline
interpretability.

### 3. Source-framework metadata extraction (bigger change, strongest signal)

**Files:** ingestion pipeline + `nodes/reduce.py`

**Change:** during ingestion, extract sub-library affiliation per doc from the
URL path (e.g. `/langgraph/...` → LangGraph, `/deepagents/...` → DeepAgents,
`/langchain/...` → LangChain core). Pass to the reduce prompt as a per-cluster
tag (e.g. "majority library: LangGraph (87% of docs)").

**Impact:** reduce gets an EXPLICIT signal for library-vs-theme decisions,
not just inferred from labels/keywords. Most accurate library-grouping
behavior. Also useful downstream for per-library navigation in the final
distilled output.

**Effort:** medium. Needs:
- ingestion-side regex per framework catalog entry to map URLs → library
- per-cluster majority-library aggregation in reduce.py
- prompt template extension
- new `cluster_library_tags_ref` MinIO blob (or in-state since small)

**Risk:** brittle URL-pattern maintenance per framework; doesn't help when
docs are in a flat URL space (e.g. single-library frameworks).

## Recommended ship order when revisited

1. **Ship #1 alone first** (adaptive min_cluster_size) — fixes pydantic
   immediately, costs nothing for langchain stack.
2. **Ship #2 second** (library-aware reduce prompt) — gives the bandit-LLM
   the flexibility to library-organize when content warrants it.
3. **Ship #3 only if needed** (URL metadata) — after observing whether #2's
   prompt hint actually produces library-organized chapters on stacks.

## Related context

- See `docs/PLANNER-ARCHITECTURE-2026-05-17.md` for the 9-node LITA-pattern
  architecture committed before these observations.
- Cluster node: research-validated UMAP(n_components=10, cosine, min_dist=0)
  + HDBSCAN(cluster_selection_method='eom', prediction_data=True).
- Reduce node: TnT-LLM single-call + USC vote + 1 self-refine + coverage
  repair pattern (per research agent brief).

---

## Resolution — Option #1 shipped (2026-05-18 PM)

Option #1 (adaptive `min_cluster_size`) shipped, but the FIRST formula
backfired. Final committed formula is sqrt-capped, driven by a May-2026
SOTA research pass. Three-revision history captured so future-me doesn't
re-litigate.

### v1 — hardcoded `_HDBSCAN_MIN_CLUSTER = 8` (pre-fix baseline)

| Framework | n_docs | mcs | n_clusters | n_chapters |
|---|---|---|---|---|
| pydantic | 85 | 8 | 3 | 2 |
| langchain stack | 744 | 8 | 19 | 5 |

Problem: pydantic too thin (the case that motivated this backlog item).

### v2 — linear `max(3, N // 15)` (shipped morning, REVERTED same day)

| Framework | n_docs | mcs | n_clusters | n_chapters | Outcome |
|---|---|---|---|---|---|
| pydantic | 85 | 5 | 4 | 4 | marginal win (2→4 chapters) |
| langchain stack | 744 | **49** | **4** | **3** | **REGRESSION — mega-cluster collapse** |

Failure mode: at N=744, linear formula demands a density mode of 49
points. In 10-D UMAP space, narrow sub-topics (e.g. DeepAgents) lack
that many neighbors → absorbed into mega-clusters. 19→4 collapse;
DeepAgents content disappeared into the agent-architecture mega-cluster.

Root cause: `min_cluster_size` is fundamentally about density geometry,
NOT corpus size. Linear scaling violates the density-floor invariant
that HDBSCAN relies on.

### v3 — sqrt-capped `max(5, min(15, ceil(sqrt(N) / 3)))` (current)

| Framework | n_docs | mcs | n_clusters | n_chapters | Outcome |
|---|---|---|---|---|---|
| pydantic | 85 | 5 | 4 | 3 | small-corpus floor preserved |
| langchain stack | 744 | **10** | **19** | **7** | **regression fixed; richer outline** |

Concrete sizing across the supported corpus range:

```
N=   30   →   mcs = 5    (floor)
N=   85   →   mcs = 5    (pydantic)
N=  250   →   mcs = 6    (terragrunt-class)
N=  500   →   mcs = 8
N=  744   →   mcs = 10   (langchain stack)
N= 1500   →   mcs = 13
N= 3000   →   mcs = 15   (cap binds)
```

Rationale (per May-2026 SOTA research):
- BERTopic ships `min_topic_size=10` and gives only "near 1M docs set to
  100 or 500" guidance — no linear formula.
- The "1-2% of N" rule of thumb (≈ sqrt(N) scaling) appears across
  multiple 2025 HDBSCAN guides.
- [LLM-Assisted Topic Reduction for BERTopic, arXiv 2509.19365
  (Sep 2025)](https://arxiv.org/abs/2509.19365): explicitly endorses
  "let HDBSCAN over-fragment, let downstream LLM merge to target k."
- Floor 5 = HDBSCAN-recommended minimum for meaningful density modes.
- Cap 15 = empirical safe ceiling — beyond this, narrow sub-topics get
  absorbed (the v2 langchain failure mode).
- [DBOpt (Nature Comm Biology 2025)](https://www.nature.com/articles/s42003-025-08332-0)
  — Bayesian-opt alternative; production-ready but overkill for the
  85-3000 corpus range. Deferred unless quality plateaus.

Cache version bumped `v2 → v3` so prior blobs auto-invalidate. See
`apps/fastapi/services/docs_distiller/planner/nodes/cluster.py` for the
final docstring + math.

### v3 langchain outline (post-fix verification)

```
1.  LangChain Framework Foundations          (clusters: [12, 13])
2.  LangSmith Platform Overview              (clusters: [0, 3, 14])
3.  Prompt Engineering and Testing           (clusters: [6, 8])
4.  Evaluation and Observability             (clusters: [1, 7])
5.  Provider and Integration Ecosystem       (clusters: [15, 16, 4])
6.  Agent Architecture and Orchestration     (clusters: [9, 17, 5, 18, 2])
7.  Streaming and Generative Interfaces      (clusters: [11, 10])
```

DeepAgents (cluster #18 "Deep Agents Framework") and LangGraph (#12
"LangGraph API Design") are both visible cluster labels again, restoring
the distinguishability we lost in v2.

### v3 pydantic outline (still thin — issue migrated to reduce)

```
1.  Pydantic Model Fundamentals       (clusters: [3, 1])
2.  Pydantic Core Concepts            (clusters: [2])
3.  Integration and Tooling           (clusters: [0])
```

3 chapters from 4 clusters — reduce merged Models + Specialized Data
Types. The thin-outline issue for pydantic-class corpora is now
correctly localized to **reduce-side**, NOT cluster-side. See "Still
open" below.

---

## Still open

### Option #1.5 — reduce-side `_TARGET_K` + cluster-splitting (NEW)

**Problem:** pydantic (4 raw clusters) produces ≤4 chapters because
`reduce.py` doesn't split overrepresented clusters. Even with the v3
cluster-size fix, pydantic-class corpora hit a 4-chapter ceiling.

**Proposed fix:** per the May-2026 SOTA research, the granularity knob
should live in `reduce.py`, not `cluster.py`:

```python
def _target_chapters(n_docs: int) -> int:
    """Cognitive cap binds early — most realistic corpora cap at 12."""
    return max(4, min(12, round(2 * math.log2(n_docs))))
# N=64+ → all cap at 12
```

Plus: when `n_clusters < target_chapters`, allow reduce to SPLIT large
clusters (>30 docs) into thematic sub-chapters via per-cluster
re-clustering or LLM-driven sub-topic enumeration. Anthropic Clio /
HERCULES pattern: hierarchical reduction.

**Effort:** medium. Needs reduce prompt extension + per-cluster soft
re-segmentation logic + cache version bump.

**Defer until:** synth ships and we see whether thin-outline pydantic
actually produces unusable studies, vs just terser-but-fine ones.

### Option #2 + #3 (unchanged from original backlog)

Library-aware reduce prompt + URL-derived source-framework metadata —
still deferred, not impactful enough to prioritize over synth work.
