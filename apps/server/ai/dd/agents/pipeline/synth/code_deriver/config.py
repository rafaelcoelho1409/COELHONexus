from .... import entities


MODEL = entities.ModelConfig(max_output_tokens = 2_000, timeout_s = 120.0, temperature = 0.6)
ROTATOR_TASK = "dd_synth_code_deriver"
# The example, up to 3 checks and the answer.
MODEL_CALLS = 6
CHECK_CALL_LIMIT = 3
