# Planner architecture decision — 2026-05-17

**Status:** committed. Supersedes the LLM-only-vs-classical+LLM split implied
in `project_planner_map_replacement.md`.

## Strategic question

Should the Planner be built as:
1. Two separate strategies (LLM-only AND classical+LLM, user picks)?
2. A merged hybrid of both?
3. A different approach that beats both?

## Decision

**Don't build two separate strategies. Build ONE hybrid — the LITA-pattern
(classical clustering + LLM-in-the-loop refinement + LLM labeling +
LLM reduce).**

The 2024-2026 research literature is unambiguous: hybrid beats both pure
approaches on quality, cost, and determinism for medium-corpus document
clustering / outline generation. Pure LLM-only is strictly inferior at
our scale (100-2000 docs per framework); pure classical loses on label
coherence and final outline quality.

The "mode dropdown" we already shipped is repurposed: instead of choosing
between architecture forks, it becomes a per-node quality/speed knob.

## Research backing

| Paper | Finding |
|---|---|
| [LITA — LLM-assisted Iterative Topic Augmentation (arxiv 2412.12459)](https://arxiv.org/html/2412.12459v1) | Outperforms LDA, SeededLDA, CorEx, BERTopic, PromptTopic. Embed-cluster first; LLM refines only ambiguous boundary docs. |
| [QualIT (Amazon Science)](https://www.amazon.science/blog/unlocking-insights-from-qualitative-text-with-llm-enhanced-topic-modeling) | **50% ground-truth overlap vs 25% for LDA/BERTopic alone.** LLM-in-the-loop on classical base. |
| [LLM-Assisted Topic Reduction for BERTopic (arxiv 2509.19365)](https://arxiv.org/html/2509.19365v1) | "More diverse and coherent topics than baseline AND better quality/efficiency than end-to-end LLM." |
| [LLMxMapReduce V2/V3 (arxiv 2410.09342)](https://arxiv.org/html/2410.09342v1) | Entropy-driven test-time scaling for map-reduce on long sequences; powers SurveyGO. |
| [Routine: Structural Planning Framework (arxiv 2507.14447)](https://arxiv.org/html/2507.14447) | GPT-4o: 96.3% accuracy WITH structural planning vs 41.1% without. |
| [LLM-Guided Semantic-Aware Clustering for Topic Modeling (ACL 2025)](https://aclanthology.org/2025.acl-long.902.pdf) | LLM-guided semantic clusters beat pure embedding clusters on coherence + diversity. |

## Why pure approaches lose (concrete numbers)

| Approach | Quality | Cost | Determinism | When it might win |
|---|---|---|---|---|
| Pure LLM-only | Medium (high variance per call) | **10-30× more expensive** | Low | Tiny corpora (<50 pages) where embeddings unavailable |
| Pure classical (BERTopic / LDA / k-means) | Low-medium (good clusters, bad labels) | Very cheap | High | Quick exploratory analysis only |
| **Hybrid (LITA pattern)** | **Highest on coherence + diversity** | Cheap (~2× pure classical) | Mostly high | **Real production work** |

## The 10-node architecture

```
1. corpus_load     classical   inventory + byte stats
2. cache_lookup    classical   ⚡ FAST EXIT if cached → plan_write
3. embed_corpus    classical   one NIM pass; vectors → MinIO; carries hash
   ┌───────────────────────────────────────┐
   │ PARALLEL (LangGraph Send pattern):    │
   │   4a. off_topic    (cosine vs anchor) │
   │   4b. dedup        (MinHash bodies)   │
   └───────────────────────────────────────┘
5. cluster         classical   UMAP + HDBSCAN on stored embeddings
6. refine          LLM-big     LITA: reassign boundary docs (prob<0.5)
7. label           LLM-big     KeyLLM-style cluster labels (2-4 words)
8. reduce          LLM-big     merge clusters → 4-12 chapter outline
9. validate        classical   coverage repair, orphan check
10. plan_write     classical   persist to MinIO
```

### Why each architectural move

**Move 1 — `embed_corpus` as a dedicated node.**
`off_topic` already embeds the corpus. If `cluster` re-embeds, that's 2× NIM
cost on every Planner run. New node runs once, stores vectors as
`{key→vector}` map in MinIO under `planner/{slug}/embeddings/{manifest_hash}`.
State carries only the reference (not the 1024-D × N matrix, which would blow
up Postgres checkpoint size). Downstream nodes (`off_topic`, `cluster`)
read from this store. Re-runs on the same corpus pay zero embedding cost.

**Move 2 — `cache_lookup` at position 2 (was position 4).**
Cache key = `hash(manifest + planner_mode + threshold_config)`. On hit,
conditional edge → `plan_write` directly. Saves 100% of compute on re-runs.

**Move 3 — Parallel `off_topic ‖ dedup` via LangGraph `Send`.**
The two cheap classical filters are independent — no reason to serialize.
~2× speedup on those steps. Each node remains atomically checkpointable.

**Move 4 — Split `map` into `cluster` + `refine` + `label` (LITA pattern).**
Each independently checkpointable + replayable:
- `cluster` (classical): UMAP → HDBSCAN; produces cluster assignments +
  per-doc membership probability
- `refine` (LLM-big): for docs with `cluster_prob < 0.5` (LITA's
  "ambiguous boundary"), the rotator's big-model group reassigns to best
  cluster. Typically <20% of corpus.
- `label` (LLM-big): KeyLLM-style per cluster — 2-4 word label generated
  from top-N representative docs near centroid.

**Why big models everywhere (committed 2026-05-17):** the published
literature shows small instruct models reach the quality ceiling on
narrow structured tasks (refine, label) — but the user's
quality-over-speed rule means tokens are free, runtime isn't a concern,
and a marginal quality gain is still worth taking. The small-model group
(`kd-keylm`) stays configured in `services/llm_chain.py` as fallback if
we observe quality regressions or hit rate-limit ceilings in practice;
default is `kd-all`.

The mode dropdown then becomes meaningful per-node:
- `cluster` is always classical
- `refine` can be OFF (faster) or ON (LITA full, big model)
- `label` can be `classical` (c-TF-IDF keywords) or `LLM` (big model, coherent labels)

## Mode dropdown repurposing

Replace the conceptually-wrong "LLM-only vs Classical+LLM" with a real
quality/speed knob:

| Mode | refine | label | reduce | Use case |
|---|---|---|---|---|
| **Best quality** (default) | LLM-big | LLM-big | LLM-big | Production runs |
| **Fast** | OFF (centroid pull) | c-TF-IDF | LLM-big | Quick re-plans, exploratory |
| **Debug** | OFF | c-TF-IDF | classical-only | Comparison / baseline runs |

All LLM-big calls route through the unified `kd-all` rotator group. The
small-model `kd-keylm` group is fallback-only (engage if production
shows quality regressions or sustained rate-limit exhaustion).

Each row → a `planner_mode` value nodes read from state and branch on.

## What changes vs current code

| Existing | Status |
|---|---|
| `corpus_load` | Keep as-is |
| `off_topic` | Keep behavior, refactor to read from `embed_corpus`'s stored vectors instead of re-embedding (~30 LoC change) |
| State plumbing, cancel, UI cards, mode dropdown | All kept |
| `IMPLEMENTED` tuple in `graph.py` | Grows as each new node lands |
| `SUBSTEP_RENDERERS` in JS | Add one per new node as it ships |

| Net new files | What |
|---|---|
| `nodes/embed_corpus.py` | One-shot NIM embedding pass + MinIO store |
| `nodes/cluster.py` | UMAP + HDBSCAN |
| `nodes/refine.py` | LLM-small boundary refinement |
| `nodes/label.py` | KeyLLM labels |
| `nodes/reduce.py` (replace stub) | Big-LLM chapter merge |
| `nodes/validate.py` (replace stub) | Coverage repair |
| `nodes/plan_write.py` (replace stub) | MinIO write |

## What's NOT in scope (deferred to future iterations)

These would be marginal-gain improvements; mentioned so we don't reinvent
them when they come up:

- **Multi-agent orchestration** (researcher/planner/critic/synthesizer
  subgraphs). Would win at 5000+ doc scale across multiple domains; doesn't
  justify complexity at our scale.
- **Iterative Self-Refine inside `reduce`** ([Madaan 2023](https://arxiv.org/abs/2303.17651)
  pattern). Adds 3-5× LLM cost for marginal coherence gain. Add as a
  quality knob later, not a base architecture change.
- **Streaming / online clustering** for 10K+ page corpora. Stress-test
  current design first; this falls out as an optimization if needed.
- **Cross-encoder reranker for boundary docs** (BGE-Reranker-v2-m3).
  Cheaper + more deterministic than LLM-refine, but only ~10% of the LITA
  quality win. Defer until `refine` proves to be a cost hotspot.
- **Hierarchical clustering** (mega-clusters → sub-clusters). Probably
  over-engineering for 100-2000 docs.

## Trade-offs (honest)

| Gain | Cost |
|---|---|
| Embeddings computed once → 3× NIM savings on re-runs | One extra node (`embed_corpus`); MinIO storage for vectors |
| Cache fast-exit → 100% savings on cached corpora | Cache key design needs care (must include all knobs that affect output) |
| Parallel off_topic ‖ dedup → ~2× speedup on cheap steps | LangGraph Send pattern is slightly more verbose than sequential edges |
| LITA-split map → independent replay + quality knob per substep | 10 nodes vs 8; 2 more checkpoints per run |
| 10 nodes = better dev replay granularity | Larger Postgres checkpoint table (negligible — Postgres can handle 10× this) |

Net: strictly better on every quality + dev-velocity dimension. The only
real cost is "more files" — a code-organization cost, not a runtime cost.

## Implementation order

1. `embed_corpus` node + refactor `off_topic` to read stored vectors
2. Move `cache_lookup` to position 2 (the simplest move)
3. `cluster` node (UMAP + HDBSCAN, no LLM)
4. `refine` node (LLM-small, the LITA piece)
5. `label` node (KeyLLM)
6. `reduce` node (replace stub with real LLM-big merge)
7. `validate` node (replace stub with coverage repair)
8. `plan_write` node (replace stub with MinIO write)
9. **Optional last:** parallelize `off_topic ‖ dedup` via Send pattern
   (easiest to add once everything else works sequentially)

Per node added: append the name to `IMPLEMENTED` tuple in
`planner/graph.py` and add a `SUBSTEP_RENDERERS[idx]` entry in
`docs_distiller.js`.

## Why we're confident this is the best practical architecture

Not "theoretical best" — there's always more we could do (multi-agent,
self-refine loops, hierarchical clustering). But for the COELHO Nexus
scale (100-2000 docs per framework, single-node K8s, $0-cost rotator
constraint), this design:

- Implements the published state-of-the-art (LITA / QualIT / LLM-augmented
  BERTopic) without exotic dependencies
- Reuses every shared computation (embeddings cached once → consumed N times)
- Stays cheap on re-runs (cache fast-exit)
- Stays debuggable (atomic checkpoints per logical operation)
- Stays extensible (new nodes slot in by appending to `IMPLEMENTED`)

The next "even better" architecture would be multi-agent orchestration,
which only beats this at >5000-doc scale on multi-domain corpora — neither
of which apply to a per-framework Knowledge Distiller.
