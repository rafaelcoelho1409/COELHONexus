from dataclasses import dataclass


@dataclass(frozen = True, slots = True)
class ModelConfig:
    max_output_tokens: int = 5000
    max_retries: int = 0
    temperature: float = 0.0


MODEL = ModelConfig()
