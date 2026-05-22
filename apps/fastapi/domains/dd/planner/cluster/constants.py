from __future__ import annotations

import math


# Hyperparameters — research-recommended defaults. Tuned for the
# 100-2000-doc / variable-density topical-cluster scenario.
_UMAP_DIM            = 10
_UMAP_N_NEIGHBORS    = 15
_UMAP_MIN_DIST       = 0.0
_HDBSCAN_MIN_SAMPLES = 5
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
_CACHE_VERSION       = "v3"
