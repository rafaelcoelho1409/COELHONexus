Investigate this request against BD backend `{backend}`:

{user_request}

Read `bd://query/{backend}/schema` (or call `bd_query_get_schema` if resource reads are unavailable), call `bd_query_draft`, and inspect its validation result. If the draft is valid, call `bd_query_run` and summarize only what the returned records support. Use available book, page, section, and source fields to identify evidence. State when the query returns no evidence or a partial result; do not invent fields, citations, or conclusions. The query is read-only and server-side result limits still apply. If `live_schema_available` is false, explain that the schema was not verified against the live backend; if `live_data_available` is false, do not imply that the current index contains records.

For Qdrant, if the schema reports `collections_truncated: true`, use a listed collection and specify one collection for a count query. Do not describe a capped collection list as the complete collection catalog.
