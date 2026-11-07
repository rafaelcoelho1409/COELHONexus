from .... import entities


# The answer is tiny, but a reasoning model's <think> preamble shares the budget (97 of 131 distills were once cut before the JSON at 600).
MODEL = entities.ModelConfig(
    max_output_tokens = 1_200, 
    timeout_s = 120.0, 
    temperature = 0.2)
