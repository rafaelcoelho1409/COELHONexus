"""Triage — deterministic Phase-2 orchestrator tool. Normalizes discovery
output, dedups by arxiv_id, diversifies by source, scores, and writes
the ranked top-N back to fs."""
from __future__ import annotations

from . import domain, params, service
