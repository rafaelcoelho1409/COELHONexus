STACK_TOOLS = frozenset({"read_file", "grep", "glob"})
ROTATOR_TASK = "dd_synth_drift_detector"
# The skill, a few greps and reads of sibling chapters, and the answer.
MODEL_CALLS = 12
# The only places the detector's file tools reach (besides its skill): the rendered chapters.
READ_PATHS = ("/chapters/**",)
