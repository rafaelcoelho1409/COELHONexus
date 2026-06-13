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
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


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
    """
    scan_id:     str            = Field(description="The scan_id from the user message; carried through verbatim.")
    phases:      list[PhaseStatus] = Field(description="One entry per phase, in execution order.")
    summary:     str            = Field(description="2-3 sentence executive summary of what the scan found.")
    themes:      list[str]      = Field(default_factory=list, description="Theme names from the synthesis subagent.")
    n_findings:  int            = Field(default=0, description="Count of digest items the task body will materialize.")
