from .... import entities


MODEL = entities.ModelConfig(max_output_tokens = 2_000, timeout_s = 120.0, temperature = 0.0)
ROTATOR_TASK = "dd_synth_paragraph_patcher"
MODEL_CALLS = 3
