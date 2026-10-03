"""corpus_normalize — normalizer cache-invalidation version. Bump on any
pass change."""
from __future__ import annotations


# v2 (2026-09-29): 9th pass strips static-site permalink debris
# (`[¶](#…)` / `[](#…)`). Recorded only; no consumer gates on it yet.
# v3 (2026-10-03): that pass also strips Private-Use-Area icon-glyph
# permalinks (Sphinx RTD theme's U+F0C1 headerlink).
NORMALIZER_VERSION = 3
