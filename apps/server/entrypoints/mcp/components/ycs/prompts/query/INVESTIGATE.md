Investigate the user's question against YCS's indexed video metadata,
transcripts, transcript chunks, or knowledge graph using the {backend}
backend. First read `ycs_query_get_schema`, then draft a read-only query,
validate it, and run it with the YCS Query tools. Keep results within
the backend's maximum limit of {maximum_limit}. Cite returned video URLs
or IDs in your answer and distinguish indexed evidence from inference.

Question:
{user_request}
