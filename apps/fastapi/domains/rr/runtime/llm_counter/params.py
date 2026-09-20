"""Tunables + registries for RR's per-scan LLM counter."""
from __future__ import annotations

from .. import params as _runtime_params


LLM_COUNTERS_TTL_S: int = _runtime_params.SNAPSHOT_TTL_S

KNOWN_PHASES: tuple[str, ...] = (
    "orchestrator",
    "discovery",
    "triage",
    "deep_read",
    "graph_build",
    "synthesis",
    # "build" — code_synth's Build-tab endpoint, outside the scan pipeline
    # proper (api/v1/rr/scan/router.py's scan_finding_code).
    "build",
)

# All 5 discovery subagents share the "discovery" bucket — they fan out in parallel and belong to one pipeline node.
SUBAGENT_TYPE_TO_PHASE: dict[str, str] = {
    "discovery_arxiv":                       "discovery",
    "discovery_semantic_scholar":            "discovery",
    "discovery_huggingface_daily_papers":    "discovery",
    "discovery_hn":                          "discovery",
    "discovery_openalex":                    "discovery",
    "deep_read":                             "deep_read",
    "synthesis":                             "synthesis",
}
