"""Model limits for YCS Ask evidence review."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen = True, slots = True)
class ModelConfig:
    max_output_tokens: int = 1200
    max_retries: int = 0
    temperature: float = 0.0


MODEL = ModelConfig()
