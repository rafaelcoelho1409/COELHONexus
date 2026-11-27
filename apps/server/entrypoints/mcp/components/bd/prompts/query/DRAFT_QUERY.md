Draft a read-only query for BD backend `{backend}` that answers this request:

{user_request}

Previous query to refine, if any:
{previous_query}

Read `bd://query/{backend}/schema` (or call `bd_query_get_schema` if resource reads are unavailable), then call `bd_query_draft`. Show the returned query and validation result. Do not call `bd_query_run`; execution is a separate user decision. If validation fails, explain the returned validation error and do not present the query as ready to run. If `live_schema_available` is false, say the schema came from the declared fallback and was not verified against the live backend.
