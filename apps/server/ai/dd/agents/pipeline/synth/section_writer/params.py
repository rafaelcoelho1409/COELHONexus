STACK_TOOLS = frozenset({"read_file"})
ROTATOR_TASK = "dd_synth_section_writer"
# The draft, a few block reads, up to 3 validations and the answer.
MODEL_CALLS = 9
CHECK_CALL_LIMIT = 4
# The only place the writer's file tools reach: the section's code bank.
READ_PATHS = ("/vault/**",)
