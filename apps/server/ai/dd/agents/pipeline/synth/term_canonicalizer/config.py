from .... import entities


MODEL = entities.ModelConfig(max_output_tokens = 4_000, timeout_s = 120.0, temperature = 0.1)
ROTATOR_TASK = "dd_synth_term_canonicalizer"
MODEL_CALLS = 3
