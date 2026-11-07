from .... import entities


# One entry per chapter (up to 30), plus room for a reasoning model's <think> preamble.
MODEL = entities.ModelConfig(
    max_output_tokens = 1_200, 
    timeout_s = 120.0, 
    temperature = 0.0)
