from .... import entities


MODEL = entities.ModelConfig(
    max_output_tokens = 8_000,
    timeout_s = 180.0,
    temperature = 0.2)
