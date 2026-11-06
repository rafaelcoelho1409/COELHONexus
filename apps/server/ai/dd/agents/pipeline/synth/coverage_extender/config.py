from .... import entities


MODEL = entities.ModelConfig(max_output_tokens = 5_000, timeout_s = 180.0, temperature = 0.3)
ROTATOR_TASK = "dd_synth_coverage_extender"
MODEL_CALLS = 4
