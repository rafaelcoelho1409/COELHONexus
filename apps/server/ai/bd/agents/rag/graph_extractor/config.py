"""Model limits for evidence-grounded book graph extraction."""
from __future__ import annotations

import dataclasses


@dataclasses.dataclass(frozen = True, slots = True)
class ModelConfig:
    max_output_tokens: int = 5000
    max_retries: int = 1
    temperature: float = 0.0


MODEL = ModelConfig()
