from .... import entities


MODEL = entities.ModelConfig(max_output_tokens = 8_000, timeout_s = 120.0, temperature = 0.0)
ROTATOR_TASK = "dd_synth_code_normalizer"
# The repair, up to 2 parser checks and the answer.
MODEL_CALLS = 5
CHECK_CALL_LIMIT = 2
