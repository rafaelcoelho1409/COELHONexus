"""Model settings for BD Query generation."""
from dataclasses import dataclass


@dataclass(frozen = True, slots = True)
class ModelConfig:
    max_output_tokens: int = 2048
    max_retries: int = 0
    temperature: float = 0.0


MODEL = ModelConfig()
