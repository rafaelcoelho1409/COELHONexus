from .... import entities


# The answer is up to 40 sections; the rest of the budget is the reads and validations before it.
MODEL = entities.ModelConfig(
    max_output_tokens = 10_000, 
    timeout_s = 240.0, 
    temperature = 0.4)
