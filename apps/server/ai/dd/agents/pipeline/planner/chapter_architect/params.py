STACK_TOOLS = frozenset({
    "write_todos", 
    "ls", 
    "read_file", 
    "glob", 
    "grep", 
    "task"
})
ROTATOR_TASK = "dd_planner_chapter_architect"
# Model calls one architect run may make: the plan, the reads and searches, up to 3 score checks, the critic round and the answer.
MODEL_CALLS = 30
# Model calls one critic run may make: the score check, a handful of searches and reads, the reply.
CRITIC_MODEL_CALLS = 12
# The only places the architect's file tools reach (besides its skills): the run's working files and the read-only corpus.
READ_PATHS = ("/work/**", "/corpus/**")
# Score checks the architect may make (the prompt says 3; one more is allowed for the check after the critic's round).
SCORE_CALL_LIMIT = 4
