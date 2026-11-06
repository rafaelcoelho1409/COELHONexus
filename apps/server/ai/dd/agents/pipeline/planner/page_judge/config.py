from .... import entities


# A verdict is short, but a reasoning model spends its budget on a <think> block first: 700 tokens leave room for both.
MODEL = entities.ModelConfig(max_output_tokens = 700, timeout_s = 70.0, temperature = 0.0)
