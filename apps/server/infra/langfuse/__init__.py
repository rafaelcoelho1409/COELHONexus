"""LangFuse SDK surface — features OTel doesn't cover.

Trace ingestion is OTLP/HTTP: `infra.opentelemetry.service.init_otel` attaches the
exporter built by `service.add_exporter` here, behind the span gate in
`domain.should_keep_span`. This package owns the SDK-only features:

  service.py    — I/O shell: client singleton, sessions, scores,
                  review flags, current-span attribute setters,
                  LangChain callbacks (all fail-soft)
  domain.py    — pure span-attribute encoders
  keys.py       — `langfuse.*` attribute namespace
  prompts.py    — get_prompt(name, label, vars) cached + bulletproof fallback
  evals/        — offline evaluation suite: datasets/ (gold corpora) +
                  judges/ (one file per judge, via the chat endpoint)

Every entry point fails soft: when the LangFuse package, network, or
credentials are absent, callers get a graceful no-op + a debug log line,
never an exception. The pipeline must never break because LangFuse is down.
"""
from __future__ import annotations
from . import domain, evals, keys, params, patterns, prompts, service


__all__ = [
    "domain",
    "evals",
    "keys",
    "params",
    "patterns",
    "prompts",
    "service",
]
