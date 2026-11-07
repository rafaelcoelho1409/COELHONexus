from .... import entities


MODEL = entities.ModelConfig(
    max_output_tokens = 3_000, 
    timeout_s = 150.0, 
    temperature = 0.2)
