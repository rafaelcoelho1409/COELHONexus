"""Judges — shared rubric-judge suite; all route through the chat endpoint.

Each judge has the signature:
    async def judge(input_: dict, expected: dict, actual: dict) -> float

Return is a float score (typically 1-5 for rubric-graded, 0-1 for binary).
Failures return 0.0 — the runner records 0.0 just like any other low score
so a run still has data even when calls fail.

Shared pieces: `params.py` (caps, call kwargs, precision),
`patterns.py` (response regex), `domain.py` (pure renders, parser, novelty),
`prompts.py` (rubric catalog), `service.py` (judge invocations).
"""
from __future__ import annotations
from . import domain, params, patterns, prompts, service


__all__ = [
    "domain",
    "params",
    "patterns",
    "prompts",
    "service",
]
