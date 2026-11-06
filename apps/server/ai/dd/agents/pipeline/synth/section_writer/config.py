from .... import entities


# The heaviest generation in the pipeline: a section is up to 12 subtopics with a citation list.
MODEL = entities.ModelConfig(max_output_tokens = 9_000, timeout_s = 180.0, temperature = 0.5)
ROTATOR_TASK = "dd_synth_section_writer"
# The draft, a few block reads, up to 3 validations and the answer.
MODEL_CALLS = 9
CHECK_CALL_LIMIT = 4
# The only place the writer's file tools reach: the section's code bank.
READ_PATHS = ("/vault/**",)
