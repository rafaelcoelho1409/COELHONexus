from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen = True, slots = True)
class DynamicStepConfig:
    """One per dd_process step served by the dynamic catalog. `group` is the
    Router group name; `top_k` caps the benchmark-ranked slice; `timeout_s` is
    the per-deployment timeout — reasoning pools need longer than classification."""
    group:     str
    top_k:     int
    timeout_s: int


# Replaces the previous three parallel dicts (_DYNAMIC_TOP_K / _DYNAMIC_STEP_TO_
# GROUP / _DYNAMIC_STEP_TIMEOUT_S) that had to stay in sync by hand.
DYNAMIC_STEPS: dict[str, DynamicStepConfig] = {
    "dd-all":          DynamicStepConfig(group = "dd-all",          top_k = 30, timeout_s = 120),
    "dd-synth":        DynamicStepConfig(group = "dd-synth",        top_k = 12, timeout_s = 180),
    "dd-reduce-label": DynamicStepConfig(group = "dd-reduce-label", top_k = 10, timeout_s = 90),
}


@dataclass(frozen = True, slots = True)
class JudgeConfig:
    """ParetoBandit-driven judge tunables. dd-grader keeps grader cells separate
    from synthesizer cells (binary vs continuous reward shape, different latency
    expectations). Top-K cascade depth was bumped 5→10 (2026-05-27 P2) after
    36% of doc_distill calls saw all 5 top-ranked arms 429-saturated together."""
    kd_process:         str   = "dd-grader"
    expected_latency_s: float = 4.0
    bandit_top_k:       int   = 10


JUDGE = JudgeConfig()
