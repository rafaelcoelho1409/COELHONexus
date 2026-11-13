"""Model limits for transcript graph extraction."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen = True, slots = True)
class ModelConfig:
    max_output_tokens: int = 6000
    max_retries: int = 1
    temperature: float = 0.0


MODEL = ModelConfig()
