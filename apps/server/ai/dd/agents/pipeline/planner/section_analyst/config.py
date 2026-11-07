from .... import entities


# A section's sketch is up to 12 chapters of title, description and concepts.
MODEL = entities.ModelConfig(
    max_output_tokens = 6_000, 
    timeout_s = 240.0, 
    temperature = 0.3)
