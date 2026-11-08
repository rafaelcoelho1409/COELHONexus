from .... import entities


MODEL = entities.ModelConfig(
    max_output_tokens = 1_000, 
    timeout_s = 120.0, 
    temperature = 0.1)
