from .... import entities


MODEL = entities.ModelConfig(max_output_tokens = 3_000, timeout_s = 150.0, temperature = 0.2)
ROTATOR_TASK = "dd_synth_chapter_reviewer"
# The skill, up to 6 section reads and the answer.
MODEL_CALLS = 10
