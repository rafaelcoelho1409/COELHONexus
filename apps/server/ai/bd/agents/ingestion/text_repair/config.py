import dataclasses


@dataclasses.dataclass(frozen = True, slots = True)
class ModelConfig:
    max_output_tokens: int = 4096
    max_retries: int = 1
    temperature: float = 0.0


MODEL = ModelConfig()
