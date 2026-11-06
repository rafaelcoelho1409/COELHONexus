"""Value objects of the Docs Distiller deep agents."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen = True, slots = True)
class ModelConfig:
    """How one role calls its model: the request bound, the sampling and the rotator task its bandit cell is keyed by."""

    max_output_tokens: int
    timeout_s: float
    temperature: float = 0.0
    max_retries: int = 0


@dataclass(frozen = True, slots = True)
class Role:
    """A built agent with the skill files its input must carry (the agent reads them from its virtual filesystem)."""

    agent: Any
    files: dict[str, Any] = field(default_factory = dict)
