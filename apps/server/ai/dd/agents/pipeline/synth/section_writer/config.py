from .... import entities


# The heaviest generation in the pipeline: a section is up to 12 subtopics with a citation list.
MODEL = entities.ModelConfig(
    max_output_tokens = 9_000, 
    timeout_s = 180.0, 
    temperature = 0.5)
