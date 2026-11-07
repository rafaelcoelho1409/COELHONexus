from .... import entities


# The answer is up to 30 chapters; the rest of the budget is the tool-using turns before it.
MODEL = entities.ModelConfig(
    max_output_tokens = 12_000, 
    timeout_s = 240.0, 
    temperature = 0.3)
