"""Judges — one file per judge; all route through the rotator (free-tier).

Each judge has the signature:
    async def judge(input_: dict, expected: dict, actual: dict) -> float

Return is a float score (typically 1-5 for rubric-graded, 0-1 for binary).
Failures return 0.0 — the runner records 0.0 just like any other low score
so a run still has data even when calls fail.
"""
