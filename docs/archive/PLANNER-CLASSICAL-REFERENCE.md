# Planner — Classical reference (archived)

**Status:** archived 2026-06-02. The classical UMAP+HDBSCAN+c-TF-IDF
path described here was the planner's canonical pipeline from
2026-05-17 until 2026-05-27, when `KD_PLANNER_LLM_FIRST=true` shipped
as the default. The legacy modules (`cluster/`, `refine/`, `label/`,
`reduce/`) and the env-flag wiring were removed on 2026-06-02. This
file captures the algorithm-level reasoning so the design intent is
recoverable without resurrecting the code.

For the canonical (LLM-first) design see
`docs/DD-PLANNER-LLM-FIRST-SOTA-2026-05-27.md`.

## Why it existed — the LITA hybrid bet

The original 2026-05-17 architecture decision (`PLANNER-ARCHITECTURE-
2026-05-17.md`) committed to a HYBRID rather than pure-LLM-only or
pure-classical: classical clustering as the base, LLM-in-the-loop
refinement, LLM labeling, LLM reduce. Research backing at the time:

| Paper | Finding |
|---|---|
| LITA (arxiv 2412.12459) | Outperforms LDA / SeededLDA / CorEx / BERTopic / PromptTopic. Embed-cluster first; LLM refines only ambiguous boundary docs. |
| QualIT (Amazon Science) | 50% ground-truth overlap vs 25% for LDA/BERTopic alone with LLM-in-the-loop on classical base. |
| LLM-Assisted BERTopic (arxiv 2509.19365) | "More diverse and coherent topics than baseline AND better quality/efficiency than end-to-end LLM." |
| LLM-Guided Semantic-Aware Clustering (ACL 2025) | LLM-guided semantic clusters beat pure embedding clusters on coherence + diversity. |

The cost case at the time:

| Approach | Quality | Cost | Determinism |
|---|---|---|---|
| Pure LLM-only | Medium (high variance) | 10-30× more expensive | Low |
| Pure classical | Low-medium (bad labels) | Very cheap | High |
| Hybrid (LITA) | Highest on coherence+diversity | ~2× pure classical | Mostly high |

## The 4-node legacy middle (now deleted)

```
cluster   classical   UMAP→HDBSCAN on stored embeddings (soft-membership)
refine    LLM-big     LITA boundary-doc reassignment (prob<0.5)
label     LLM-big     KeyLLM-style 2-4 word cluster labels
reduce    LLM-big     merge clusters → 4-12 chapter outline
```

Shared head and tail (still live in the LLM-first path):
`corpus_load → embed_corpus → off_topic → [...middle...] → order_chapters → plan_write`.

### cluster — UMAP + HDBSCAN
- UMAP dim-reduction (1024-D embeddings → 5-D) for HDBSCAN density.
- HDBSCAN with `min_cluster_size = max(3, n_docs / 30)`; soft-membership
  vector per doc (`all_points_membership_vectors`).
- Output: `cluster_assignments_ref` MinIO key for the .npz blob
  (`keys`, `assignments`, `max_probs`, `soft_membership`).

### refine — LITA boundary-doc reassignment
- Identify docs with `max_prob < 0.5` (LITA boundary candidates).
- One LLM call per boundary doc: "this doc is currently in cluster K
  with prob P; given the cluster summary, is the assignment correct?"
- LLM may reassign or keep. Output: `refine_assignments_ref` .npz with
  `(keys, refined_assignments, original_assignments, decisions_json)`.

### label — KeyLLM-style cluster labels
- Per cluster: sample ~5 representative docs (highest membership prob).
- LLM call: "produce a 2-4 word label for this cluster".
- Optional round-2: LLM sees ALL round-1 labels and may rename for
  cross-cluster disambiguation. Output: `cluster_labels_ref` JSON
  `{labels: {int_id: str}, n_round2, round1_decisions}`.

### reduce — cluster → chapter outline
- Concat all labeled clusters with sample contributions.
- One LLM call: "given these N clusters, produce a 4-12 chapter
  pedagogical outline. Each chapter is one of: 1 cluster, ≥2 merged
  clusters, OR a synthesis chapter spanning clusters."
- Pydantic-validated; coverage repair (any doc in no chapter → forced
  reassignment). Output: `chapter_plan_ref` JSON outline blob.

## Why we left the LITA hybrid

Empirical drift from 2026-05-27 onward (see
`DD-PLANNER-LLM-FIRST-SOTA-2026-05-27.md`):

1. **Catastrophic under-clustering on small corpora**: Browser-Use
   38 → 2 chapters; Claude Code 130 → 2 chapters with 91% of docs in
   one cluster. HDBSCAN's density assumption breaks on uneven topic
   distributions.
2. **UMAP+HDBSCAN parameter sensitivity**: had to tune per framework;
   no setting worked across all corpora.
3. **Free-tier LLM rotator + long-context models matured**: gemini-flash
   1M-context made the "long-context LLM proposes 6-15 chapters from
   distillates" path tractable without the classical preprocessing.

The LLM-first path runs:
`doc_distill → chapter_propose → chapter_assign → chapter_select`,
which empirically:
- Eliminates the under-clustering failure mode (LLM proposes
  semantically-grouped chapters from doc distillates, not from
  density-based clusters).
- Scales smoothly 50-2000 docs.
- Costs are competitive with classical+LLM thanks to free-tier rotator
  burst capacity.

## Recovery path

If you ever need to resurrect the classical pipeline:

1. `git log --all -- apps/fastapi/domains/dd/planner/cluster/` finds
   the last commit that contained the code.
2. `git show <commit>:apps/fastapi/domains/dd/planner/cluster/node.py`
   (and equivalent for `refine`, `label`, `reduce`) prints the source.
3. Re-add `umap-learn>=0.5.8` and `hdbscan>=0.8.43` to
   `apps/fastapi/pyproject.toml`.
4. Restore the `_LEGACY_MIDDLE` tuple and `KD_PLANNER_LLM_FIRST` flag
   wiring in `apps/fastapi/domains/dd/planner/graph.py`.
5. Restore `cluster_assignments_ref`, `refine_assignments_ref`,
   `cluster_labels_ref`, plus the `*_stats` fields, in
   `apps/fastapi/domains/dd/planner/state.py`.
6. Restore the legacy hydration branch in
   `apps/fastapi/domains/dd/planner/plan_write/node.py`.
7. Restore the `_compute_manifest_hash` 5-arg signature in
   `apps/fastapi/domains/dd/planner/plan_write/service.py`.

The shape of each move is documented above; the actual code is in
git history. Don't keep dead code in the tree — `git log` is the
deprecation archive.
