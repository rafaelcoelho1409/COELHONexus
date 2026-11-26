Review BD Agentic Ingestion run `{agentic_run_id}` for book `{book_id}`.

Read `bd://books/{book_id}/ingestion/runs/{agentic_run_id}` and `bd://books/{book_id}/ingestion/runs/{agentic_run_id}/quality`. Use `bd_ingestion_get_run` and `bd_ingestion_get_quality_summary` if needed.
Summarize run status, unfinished or failed stages, review and unverified page counts, and any quality limitation.
Separate observed manifest facts from your interpretation. Do not infer page-level content from aggregate counts.
Do not start, resume, cancel, or replay a run during this review.
