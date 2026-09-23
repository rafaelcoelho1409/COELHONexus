"""LangFuse SDK surface — features OTel doesn't cover.

Trace ingestion flows through `infra.otel.service.add_langfuse_exporter`
(OTLP/HTTP). This package owns the SDK-only features:

  service.py    — I/O shell: client singleton, sessions, scores,
                  review flags, LangChain callbacks (all fail-soft)
  spans.py      — current-span LangFuse attribute setters
  domain.py     — pure span-attribute encoders
  keys.py       — `langfuse.*` attribute namespace
  prompts.py    — get_prompt(name, label, vars) cached + bulletproof fallback
  evals/        — offline evaluation suite: datasets/ (gold corpora) +
                  judges/ (one file per judge, via the chat endpoint)

Every entry point fails soft: when the LangFuse package, network, or
credentials are absent, callers get a graceful no-op + a debug log line,
never an exception. The pipeline must never break because LangFuse is down.
"""
from __future__ import annotations
from . import domain, evals, keys, params, patterns, prompts, service, spans


__all__ = [
    "domain",
    "evals",
    "keys",
    "params",
    "patterns",
    "prompts",
    "service",
    "spans",
]
