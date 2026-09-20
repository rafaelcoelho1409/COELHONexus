"""Tunables for the RR agent's code_synth tool."""
from __future__ import annotations


# Bump this on every prompt or refine-logic change so MinIO cache misses
# repopulate against the new behavior. Old objects sit alongside the new
# ones (cheap) — operator can wipe `rr/scans/{id}/code/` to GC.
CODE_SYNTH_PROMPT_VERSION: str = "v2"

# Per-call bound for one generate/critique/revise round — full paper
# context in, 150-400 line file out. 2 attempts: a transient NIM
# timeout/connection blip gets one retry via `resilient_ainvoke` rather
# than failing the whole 3-round synthesis outright.
CALL_TIMEOUT_S: float = 180.0
