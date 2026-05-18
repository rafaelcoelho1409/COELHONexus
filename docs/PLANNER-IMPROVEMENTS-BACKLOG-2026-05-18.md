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
