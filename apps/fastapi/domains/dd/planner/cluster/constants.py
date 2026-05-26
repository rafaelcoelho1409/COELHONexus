from __future__ import annotations

import math


# Hyperparameters — research-recommended defaults. Tuned for the
# 100-2000-doc / variable-density topical-cluster scenario.
#
# Bundle 5a (2026-05-25): UMAP/HDBSCAN retune per BERTopic best practices
# + HDBSCAN docs + BERTopic discussion #600 for small framework corpora.
#   - n_components 10 → 5: HDBSCAN density estimates degrade in >5-D for
#     small corpora; BERTopic official best practice is n_components=5.
#   - n_neighbors 15 → 30: bias toward global topical structure (cite:
#     BERTopic_Teen empirical PMC12378273, BERTopic disc #600). At 15
#     UMAP over-emphasizes local neighborhoods → fragments topical clusters.
#   - cluster_selection_epsilon = 0.2 (NEW): on L2-normalized cosine
#     embeddings, ε in [0.2, 0.3] merges density-similar siblings without
#     collapsing meaningful clusters. Was unset before → HDBSCAN over-
#     fragmented similar-density clusters into 14 groups when 8 was right.
_UMAP_DIM                  = 5            # was 10
# n_neighbors is the CAP — actual value is adaptive per-corpus via
# `_adaptive_n_neighbors(n_docs)` in service.py. 30 is fine for ≥120 docs
# (Claude Code, FastMCP, LangChain), but for tiny corpora (Browser Use
# N=38) it destroys local density structure → HDBSCAN labels everything
# noise → 0 chapters. Adaptive formula bounds it to n_docs // 4 for
# small N. See `_adaptive_n_neighbors` for full rationale.
_UMAP_N_NEIGHBORS_CAP      = 30
# Legacy alias kept for any direct importers; equals the cap.
_UMAP_N_NEIGHBORS          = _UMAP_N_NEIGHBORS_CAP
_UMAP_MIN_DIST             = 0.0
_HDBSCAN_MIN_SAMPLES       = 5
_CLUSTER_SELECTION_EPSILON = 0.2          # NEW — merge density-similar siblings
# A boundary doc has max-prob below this floor; LITA's `refine` node
# will re-evaluate those via LLM-small reassignment to the best cluster.
_BOUNDARY_PROB_FLOOR = 0.5
_BLOB_PREFIX         = "planner"
# Cache schema version — bump on hyperparameter formula change so old
# blobs invalidate cleanly.
#   v2 (2026-05-18 AM): linear adaptive min_cluster_size — backfired at
#                       large scale (langchain 744 docs → mcs=49 → mega-
#                       cluster collapse, 19→4 clusters).
#   v3 (2026-05-18 PM): sqrt-capped formula per May-2026 SOTA research.
#   v4 (2026-05-25):    UMAP n_components 10→5, n_neighbors 15→30,
#                       HDBSCAN cluster_selection_epsilon 0.2 — Bundle 5a.
#   v5 (2026-05-26):    UMAP n_neighbors is now adaptive (small-corpus
#                       failure on Browser Use N=38 → all-noise). See
#                       _adaptive_n_neighbors() in service.py.
_CACHE_VERSION       = "v5-2026-05-26"
