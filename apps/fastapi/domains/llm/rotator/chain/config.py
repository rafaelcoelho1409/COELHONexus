from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen = True, slots = True)
class DynamicStepConfig:
    group:     str
    top_k:     int
    timeout_s: int


DYNAMIC_STEPS: dict[str, DynamicStepConfig] = {
    "dd-all":          DynamicStepConfig(group = "dd-all",          top_k = 30, timeout_s = 120),
    "dd-synth":        DynamicStepConfig(group = "dd-synth",        top_k = 12, timeout_s = 180),
    "dd-reduce-label": DynamicStepConfig(group = "dd-reduce-label", top_k = 10, timeout_s = 90),
}


@dataclass(frozen = True, slots = True)
class JudgeConfig:
    """dd-grader cells kept separate from synthesizer cells — binary vs continuous reward shape."""
    dd_process:         str   = "dd-grader"
    expected_latency_s: float = 4.0
    bandit_top_k:       int   = 10


JUDGE = JudgeConfig()
