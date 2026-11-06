from .... import entities


# A digest is small, but a reasoning model's <think> preamble shares the budget.
MODEL = entities.ModelConfig(max_output_tokens = 7_000, timeout_s = 180.0, temperature = 0.1)
ROTATOR_TASK = "dd_synth_source_router"
# The digest, up to 3 validations and the answer.
MODEL_CALLS = 7
CHECK_CALL_LIMIT = 3
