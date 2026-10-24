"""ycs/query keys — Redis schema-cache key pattern + Postgres history table."""
from __future__ import annotations


SCHEMA_KEY = "coelhonexus:ycs:query:schema:{backend}:v3"


# + filter when SSO lands; the schema below already accommodates it as a
# nullable text.

HISTORY_TABLE_NAME = "query_history"
