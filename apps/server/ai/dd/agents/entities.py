"""Value objects of the Docs Distiller deep agents."""
from __future__ import annotations

import dataclasses


@dataclasses.dataclass(frozen = True, slots = True)
class ModelConfig:
    """How one role calls its model: the request bound, the sampling and the rotator task its bandit cell is keyed by."""

    max_output_tokens: int
    timeout_s: float
    temperature: float = 0.0
    max_retries: int = 0
