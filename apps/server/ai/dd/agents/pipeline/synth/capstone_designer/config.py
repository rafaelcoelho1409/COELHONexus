from .... import entities


MODEL = entities.ModelConfig(
    max_output_tokens = 4_000,
    timeout_s = 180.0,
    temperature = 0.3)
