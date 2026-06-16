"""Pydantic boundary schemas for DeepAgents' `response_format=` enforcement.

Per `create_deep_agent(response_format=ScanComplete)`: the agent's final
output is run through `ScanComplete.model_validate(...)` and the agent
WILL NOT terminate with an output that doesn't match. This eliminates a
whole class of orchestrator failures ("the model returned prose instead
of structured output") at the framework layer instead of in our task
post-processing.

If DeepAgents v0.6's `response_format` doesn't accept Pydantic v2 models
directly (some versions need a JSON schema dict), we have a compatibility
fallback in agent/graph.py that calls `.model_json_schema()`.

2026-06-15: Added DigestSchema. The report subagent now binds it via
SubAgent.response_format so the LLM cannot emit malformed JSON (the
`Invalid \\uXXXX escape` / `Expecting ',' delimiter` bug class). The
framework's tool-strategy injects a `respond_in_format` tool the LLM
must call to terminate, with Pydantic-validated args.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, model_validator


# Phase names the orchestrator MUST cover before it's allowed to emit
# ScanComplete. Used by the completeness validator (2026-06-15) to force
# the LLM to actually dispatch synthesis rather than terminate after
# deep_read. The PhaseEnforcer middleware is the fs-level truth check;
# this Pydantic gate is the cheap structural layer.
#
# 2026-06-16 (post-f52fb84a): `report` dropped — synthesis now owns
# per-paper themes via write_synthesis_report.per_paper_themes, and
# `_build_digest_from_fs` assembles the digest in Python. The report
# subagent isn't dispatched anymore, so requiring it in phases would
# force a false claim from the orchestrator.
_REQUIRED_PHASES: frozenset[str] = frozenset({
    "discovery", "triage", "deep_read", "graph_build", "synthesis",
})


class PhaseStatus(BaseModel):
    """One phase's completion + count metadata."""
    phase:    Literal[
        "discovery", "triage", "deep_read", "graph_build",
        "synthesis", "report",
    ]
    completed: bool   = Field(description="True iff the phase wrote its expected fs artifacts.")
    items:     int    = Field(default=0, description="Per-phase count (papers stashed, extractions written, etc.).")
    note:      str    = Field(default="", description="Short freeform diagnostic note from the phase.")


class ScanComplete(BaseModel):
    """The orchestrator's final structured output.

    The orchestrator MUST emit this shape as its last message. DeepAgents
    enforces this via response_format — if the orchestrator returns prose,
    the framework re-prompts until it produces a valid ScanComplete.

    The fields are intentionally MINIMAL — we don't try to embed the full
    digest here (that's persisted via the dedicated path). What we capture
    is the orchestrator's own status report.

    2026-06-15 COMPLETENESS GATE: a `@model_validator(mode='after')` REJECTS
    the response if synthesis or report are missing/not-completed from
    `phases`, or if `n_findings == 0`, or if the summary is a placeholder
    (the `_build_digest_from_fs` fallback "Top N papers from this radar
    scan" string is 37 chars — we require >50). This eliminates the
    "agent terminates after deep_read" failure mode observed in scan
    fd48309a (309s but degraded=synthesis_missing). The framework
    re-prompts the LLM with the validator's error message until it
    produces a complete ScanComplete. Combined with PhaseEnforcer's
    before_model hook (Fix #2), the orchestrator can no longer skip
    phases.
    """
    scan_id:     str               = Field(description="The scan_id from the user message; carried through verbatim.")
    phases:      list[PhaseStatus] = Field(description="One entry per phase, in execution order.")
    summary:     str               = Field(min_length=50, description="2-3 sentence executive summary of what the scan found. MUST be the synthesis subagent's `summary` field, NOT a placeholder. Min 50 chars.")
    themes:      list[str]         = Field(default_factory=list, description="Theme names from the synthesis subagent. Must be non-empty unless every phase legitimately had 0 candidates.")
    n_findings:  int               = Field(ge=1, description="Count of digest items the task body will materialize. Must be >=1 — emit ScanComplete only when ranked findings exist.")

    @model_validator(mode="after")
    def _require_complete_phases(self) -> "ScanComplete":
        """Reject the response if the orchestrator is trying to terminate
        without having dispatched synthesis + report."""
        completed_phases = {
            ps.phase for ps in self.phases if ps.completed
        }
        missing = _REQUIRED_PHASES - completed_phases
        if missing:
            raise ValueError(
                f"Cannot emit ScanComplete: phases {sorted(missing)!r} are not "
                f"marked completed=True. Dispatch the missing subagents/tools "
                f"BEFORE emitting respond_in_format. The Research Radar scan "
                f"is not done until synthesis writes themes and report writes "
                f"the digest."
            )
        if not self.themes:
            raise ValueError(
                "Cannot emit ScanComplete with empty themes list. Dispatch the "
                "synthesis subagent (task(subagent_type='synthesis', ...)) — it "
                "must write fs/synthesis/report.json with themes BEFORE you "
                "respond_in_format."
            )
        return self


# --------------------------------------------------------------------------- #
# Digest schemas — bound to the report subagent via response_format= so the
# LLM's terminal output is structurally validated. Mirrors the shape in
# agent/skills/digest_rendering.md (which still drives the LLM's behaviour
# during the gather-data phase). Schema doc-strings ARE the prompt for the
# LLM-driven respond_in_format() tool call.
# --------------------------------------------------------------------------- #
class DigestExtraction(BaseModel):
    """Per-paper deep_read extraction, lifted from fs/extractions/{arxiv_id}.json."""
    arxiv_id:     str   = Field(description="Canonical arXiv id (no version suffix).")
    problem:      str   = Field(default="", description="2-3 sentences — what real-world gap the paper closes.")
    method:       str   = Field(default="", description="4-6 sentences — how the paper does it.")
    math:         str   = Field(default="", description="Key formulas (LaTeX) + their role.")
    how_to_build: str   = Field(default="", description="Implementation notes — what to wire to what.")
    money_angle:  str   = Field(default="", description="Commercial / portfolio applicability.")
    confidence:   float = Field(default=0.0, ge=0.0, le=1.0, description="Self-rated extraction confidence in [0, 1].")


class DigestItem(BaseModel):
    """One ranked paper in the digest. See skills/digest_rendering.md for
    field provenance + the per-item themes hard-rules."""
    arxiv_id:    str                       = Field(description="Canonical arXiv id (no version suffix).")
    rank:        int                       = Field(ge=1, description="1 = best (highest signal); contiguous from 1.")
    signal:      float                     = Field(description="Signal score from triage — copy verbatim, don't re-score.")
    title:       str                       = Field(description="Paper title.")
    authors:     list[str]                 = Field(default_factory=list, description="Author list.")
    summary:     str                       = Field(description="ONE sentence: what's new in this paper. Often the extraction's `problem` truncated.")
    themes:      list[str]                 = Field(default_factory=list, description="STRICT SUBSET of top-level themes — max 2, often 0-1. NEVER copy the full top-level list. See digest_rendering.md hard-rules.")
    sources:     list[str]                 = Field(default_factory=list, description="Discovery sources where this paper appeared (e.g. ['arxiv', 'hn']).")
    extraction:  DigestExtraction | None  = Field(default=None, description="Deep-read extraction; None if deep_read was skipped or failed.")


class DigestSchema(BaseModel):
    """The report subagent's terminal structured output.

    Bound via SubAgent(response_format=DigestSchema) — DeepAgents' ToolStrategy
    injects a `respond_in_format` tool the LLM must call to terminate. Pydantic
    validates fields; the LLM is RE-PROMPTED on validation failure rather than
    being allowed to emit prose. This eliminates the `\\uXXXX` / missing-comma
    JSON failures we were burning ~10min/scan on.

    Field mappings (see skills/digest_rendering.md):
      - scan_id    : pulled from the task description verbatim
      - summary    : lifted from synthesis/report.json `summary` — don't re-write
      - themes     : lifted from synthesis/report.json `themes`   — preserve order
      - items      : ranked list, one per top-N paper from triage/top_n.json
                     MUST be non-empty (`min_length=1`). Scan 0b160aec showed
                     the LLM emit a valid-but-empty `{items: []}` after a
                     successful one, overwriting the good digest. The
                     min_length gate forces the framework to re-prompt
                     instead of accepting the empty payload.
      - themes     : MUST be non-empty (`min_length=1`). Same reasoning as
                     items — a degenerate empty-themes emission would
                     wipe out the synthesis work already on disk.
    """
    scan_id:    str              = Field(description="UUID from the task description; preserve verbatim.")
    summary:    str              = Field(min_length=50, description="2-3 sentence executive summary, lifted from synthesis. Min 50 chars — placeholder strings (e.g. 'Top N papers') won't pass.")
    themes:     list[str]        = Field(min_length=1, description="Theme names from synthesis — same ordering. MUST be non-empty; copy them verbatim from fs/synthesis/report.json.")
    items:      list[DigestItem] = Field(min_length=1, description="Ranked digest items; one per top-N paper. MUST be non-empty — every scan has at least 1 finding, and emitting [] would overwrite a prior good write_digest call.")
