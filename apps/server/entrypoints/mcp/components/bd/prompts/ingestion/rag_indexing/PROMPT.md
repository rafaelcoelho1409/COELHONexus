Diagnose BD Agentic RAG indexing run `{agentic_run_id}` for book `{book_id}`.

Read `bd://books/{book_id}/rag/index-runs/{agentic_run_id}` and `bd://books/{book_id}/rag/current-index`. Use `bd_rag_index_get_run` and `bd_rag_index_get_current` if needed.
Identify the current or failed graph stage, compare completed backend counts, and state whether a validated current index exists.
Recommend a replay node only when the run state and completed predecessors support it. Treat that as a recommendation for an operator;
do not call resume, cancel, or replay tools.
