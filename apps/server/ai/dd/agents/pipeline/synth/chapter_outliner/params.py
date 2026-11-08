STACK_TOOLS = frozenset({
    "read_file", 
    "grep", 
    "glob"
})
ROTATOR_TASK = "dd_synth_chapter_outliner"
# Model calls one run may make: the skill, a few page reads, up to 4 validations and the answer.
MODEL_CALLS = 24
# The only places the outliner's file tools reach (besides its skill): the chapter's source pages.
READ_PATHS = ("/sources/**",)
# Validations the outliner may ask for (the prompt says 4).
CHECK_CALL_LIMIT = 5
