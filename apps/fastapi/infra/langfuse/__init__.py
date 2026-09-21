"""LangFuse SDK surface — features OTel doesn't cover.

Trace ingestion flows through `infra.otel.exporters.add_langfuse_exporter`
(OTLP/HTTP). This package owns the SDK-only features:

  service.py    — lazy `Langfuse` singleton (env-driven)
  sessions.py   — context manager: session_id + user_id (+ baggage)
  scores.py     — record_score(trace_id, name, value, comment)
  prompts.py    — get_prompt(name, label, vars) cached + bulletproof fallback
  spans.py      — current-span LangFuse attribute setters
  domain.py     — pure span-attribute encoders
  datasets/     — uploader + runner for gold corpora
  evals/judges/ — one file per judge (all route through the chat endpoint)

Every entry point fails soft: when the LangFuse package, network, or
credentials are absent, callers get a graceful no-op + a debug log line,
never an exception. The pipeline must never break because LangFuse is down.
"""
from __future__ import annotations

from . import annotation, callbacks, datasets, domain, evals, params, patterns, prompts, scores, service, sessions, spans


__all__ = [
    "annotation",
    "callbacks",
    "datasets",
    "domain",
    "evals",
    "params",
    "patterns",
    "prompts",
    "scores",
    "service",
    "sessions",
    "spans",
]
