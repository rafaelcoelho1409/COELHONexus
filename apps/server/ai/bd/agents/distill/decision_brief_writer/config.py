from dataclasses import dataclass


@dataclass(frozen = True, slots = True)
class ModelConfig:
    max_output_tokens: int = 3500
    max_retries: int = 0
    temperature: float = 0.0
    timeout_s: float = 180


MODEL = ModelConfig()
