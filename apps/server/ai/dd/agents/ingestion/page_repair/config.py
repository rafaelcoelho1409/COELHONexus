from dataclasses import dataclass


@dataclass(frozen = True, slots = True)
class ModelConfig:
    max_output_tokens: int = 4096
    max_retries: int = 2
    temperature: float = 0.0


MODEL = ModelConfig()
