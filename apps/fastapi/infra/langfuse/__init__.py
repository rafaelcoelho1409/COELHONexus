"""LangFuse SDK surface — features OTel doesn't cover.

Trace ingestion flows through `infra.otel.exporters.add_langfuse_exporter`
(OTLP/HTTP). This package owns the SDK-only features:

  client.py     — lazy `Langfuse` singleton (env-driven)
  sessions.py   — context manager: session_id + user_id (+ baggage)
  scores.py     — record_score(trace_id, name, value, comment)
  prompts.py    — get_prompt(name, label, vars) cached + bulletproof fallback
  datasets/     — uploader + runner for gold corpora
  evals/judges/ — one file per judge (all route through the rotator)

Every entry point fails soft: when the LangFuse package, network, or
credentials are absent, callers get a graceful no-op + a debug log line,
never an exception. The pipeline must never break because LangFuse is down.
"""
from __future__ import annotations

from .client import get_client, is_available


__all__ = ["get_client", "is_available"]
