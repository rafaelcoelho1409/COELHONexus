from __future__ import annotations

import re


# Target chapter count — soft target in prompt prose, hard bounds in schema.
_TARGET_K        = 8
_K_MIN           = 4
_K_MAX           = 12
# N samples + USC vote.
_N_SAMPLES       = 3
_TEMPERATURE     = 0.3
# Per-call max_tokens. Outline JSON for 19 clusters → 4-12 chapters with
# titles + descriptions + member lists is comfortably under 4K tokens.
_MAX_TOKENS_OUTLINE = 4000
_MAX_TOKENS_VOTE    = 200
_MAX_TOKENS_REFINE  = 4000
_MAX_TOKENS_REPAIR  = 4000
# c-TF-IDF settings (reuse refine.py's helper).
_KEYWORDS_PER_CLUSTER = 5
_CTFIDF_DOC_CHARS     = 1200
# Rep doc first-line snippet length per cluster (one short line).
_REP_DOC_CHARS = 160
# Coverage repair budget — TnT-LLM reports 12% silent-drop rate; up to 3
# repair retries should handle the vast majority. After that we
# force-repair: dump orphans into Miscellaneous + log a warning.
_MAX_REPAIR_RETRIES = 3
# Cache invalidation bump.
_PROMPT_VERSION  = "v1-2026-05-18"
_BLOB_PREFIX     = "planner"

_MISC_CHAPTER_TITLE = "Miscellaneous"

_JSON_RE = re.compile(r"\{.*\}", re.DOTALL)
