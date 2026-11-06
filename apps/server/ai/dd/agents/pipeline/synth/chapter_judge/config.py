from .... import entities


MODEL = entities.ModelConfig(max_output_tokens = 3_000, timeout_s = 240.0, temperature = 0.0)
# The judge keeps its own Rotator cell: the node reports a lenient verdict back to it (`report_quality`).
ROTATOR_TASK = "dd.judge"
MODEL_CALLS = 3
