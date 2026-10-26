"""corpus_normalize — normalizer cache-invalidation version. Bump on any
pass change."""
from __future__ import annotations


# v2 (2026-09-29): 9th pass strips static-site permalink debris
# (`[¶](#…)` / `[](#…)`). Recorded only; no consumer gates on it yet.
# v3 (2026-10-03): that pass also strips Private-Use-Area icon-glyph
# permalinks (Sphinx RTD theme's U+F0C1 headerlink).
# v4 (2026-10-03): new pass hoists fences nested in Sphinx definition-list
# bodies (4-space indented, invisible to CommonMark/the vault) to column 0.
# v5 (2026-10-26): Mintlify attrs are stripped from fence openers inside blockquotes/callouts too (`> ```bash theme={…}`
# kept them); new pass drops `<video>`/`<audio>` elements that lost their `src`.
NORMALIZER_VERSION = 5
