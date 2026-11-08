from .... import entities


MODEL = entities.ModelConfig(
    max_output_tokens = 18_000, 
    timeout_s = 300.0, 
    temperature = 0.2)
