from .... import entities


# Some Rotator models spend their budget restating the task before the JSON; the answer is up to 20 claims and a term list.
MODEL = entities.ModelConfig(max_output_tokens = 4_000, timeout_s = 120.0, temperature = 0.0)
ROTATOR_TASK = "dd_synth_claim_extractor"
MODEL_CALLS = 3
