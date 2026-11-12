import dataclasses


@dataclasses.dataclass(frozen = True, slots = True)
class ModelConfig:
    max_output_tokens: int = 3072
    max_retries: int = 2
    temperature: float = 0.0


MODEL = ModelConfig()
