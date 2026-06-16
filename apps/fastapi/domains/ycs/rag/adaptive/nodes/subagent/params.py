"""ycs/rag/adaptive/nodes/subagent — sub-agent tunables.

Per `docs/CODE-CONVENTIONS.md` §2: loose numeric tunables live in
`params.py`. None of these fit a frozen-dataclass group yet — they
describe independent concerns (overall sub-agent budget vs the
rephrase retry's own LLM budget)."""
from __future__ import annotations


# 10 min ceiling on a single sub-agent's STANDARD pipeline run.
# Worst-case happy-path is ~13 min (recursion_limit=12 × per-node
# wall-time after our other timeouts), but in sequential mode
# (`SUBAGENT_CONCURRENCY=1`) one stuck sub-agent blocks every other
# sub-question for the same duration; cap at 10 min so the next
# sub-question can run before the parent watchdog (15 min) trips and
# kills the whole turn. Lands in `sub_results` with
# `error_kind="timeout"` so the UI placeholder reads correctly.
SUBAGENT_RUNTIME_TIMEOUT_S = 10 * 60.0


# 30 s ceiling on the rephrase LLM call (single short structured
# completion). Generous enough for one rotator fallback if the first
# arm 429s, tight enough that a failed rephrase doesn't eat the
# sub-agent's overall budget — if the rephrase itself hangs we'd
# rather skip the retry and report `no_docs` on the first attempt's
# placeholder than wait minutes for a question rewriter.
REPHRASE_TIMEOUT_S = 30.0
