"""infra/postgres — Postgres connection config, connection factory and the shared LangGraph checkpointer.

Every domain and entrypoint reaches Postgres through here; no other module
imports `psycopg` / `psycopg_pool` / `langgraph.checkpoint.postgres`, reads the
`POSTGRES_*` env vars or builds a connection URL. Domains keep owning their SQL
(tables, queries) and what they store."""
from . import domain, params, service


__all__ = ["domain", "params", "service"]
