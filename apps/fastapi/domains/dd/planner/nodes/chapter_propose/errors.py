"""chapter_propose errors — module exception classes (subclass RuntimeError so
generic `except RuntimeError` callers keep working)."""
from __future__ import annotations


class PlannerDegradedAbort(RuntimeError):
    """Raised (opt-in, DD_PLANNER_ABORT_ON_DEGRADE) when chapter_propose fell back
    on a large corpus — surfaces as a normal terminal 'failed' with a clear
    reason instead of grinding chapter_assign for an unusable plan."""
