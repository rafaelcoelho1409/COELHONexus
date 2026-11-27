Draft one read-only {backend} query for the YCS data described by the
live schema returned by `ycs_query_get_schema`. Keep the query scoped to
YCS namespaces. Use only fields, labels, payload keys, and vector names
present in that schema. Respect the backend result cap of {maximum_limit}.
Never create, modify, or delete data. Return only the query body in the
backend's required format.

User request:
{user_request}

Previous query to refine, if any:
{previous_query}
