Review BD Agentic Ingestion run `{agentic_run_id}` for book `{book_id}`.

Start with `bd://books/{book_id}/pipeline/readiness` or `bd_book_pipeline_status` for one current snapshot across extraction, Ingestion, indexing, and embedding compatibility. Read `bd://books/{book_id}/ingestion/runs/{agentic_run_id}` and `bd://books/{book_id}/ingestion/runs/{agentic_run_id}/quality` for run detail. Use `bd_ingestion_get_run` and `bd_ingestion_get_quality_summary` if needed.

Start a new run with `bd_ingestion_start_run`; Ingestion queues RAG indexing after publishing. Follow progress with `bd_ingestion_get_run` and `bd_rag_index_get_run`, and check the published index with `bd_rag_index_get_current`. Use `bd_ingestion_resume_run` or `bd_rag_index_resume` only when asked to resume. Do not start, resume, cancel, or replay a run during this review.
Summarize run status, unfinished or failed stages, review and unverified page counts, and any quality limitation.
Separate observed manifest facts from your interpretation. Do not infer page-level content from aggregate counts.
