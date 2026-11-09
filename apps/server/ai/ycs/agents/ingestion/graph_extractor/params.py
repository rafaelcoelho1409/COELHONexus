"""Graph extraction agent limits."""

ROTATOR_TASK = "ycs_ingestion_graph_extractor"
MAX_MODEL_CALLS = 3
MAX_ATTEMPTS = 2
RETRY_WAIT_BASE_S = 1.0
MODEL_MAX_OUTPUT_TOKENS = 6000
MODEL_MAX_RETRIES = 1
