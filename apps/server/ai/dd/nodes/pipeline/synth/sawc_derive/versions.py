"""sawc_derive — schema + prompt cache-invalidation markers."""
from __future__ import annotations


SAWC_DERIVE_SCHEMA_VERSION = "1.0"
# v3 (2026-11-06): a code_deriver agent writes the example and its explanation in one run and checks the code with a parser tool (was N samples + a rank + a re-explain call).
SAWC_DERIVE_PROMPT_VERSION = "v3-deriver-agent-2026-11-06"
