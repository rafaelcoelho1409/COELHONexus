from .... import entities


# The answer is up to 40 sections; the rest of the budget is the reads and validations before it.
MODEL = entities.ModelConfig(max_output_tokens = 10_000, timeout_s = 240.0, temperature = 0.4)
ROTATOR_TASK = "dd_synth_chapter_outliner"
# Model calls one run may make: the skill, a few page reads, up to 4 validations and the answer.
MODEL_CALLS = 16
# The only places the outliner's file tools reach (besides its skill): the chapter's source pages.
READ_PATHS = ("/sources/**",)
# Validations the outliner may ask for (the prompt says 4).
CHECK_CALL_LIMIT = 5
