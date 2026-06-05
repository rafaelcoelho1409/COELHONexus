"""Synth observability — OTel span wrapping for LangGraph node coros."""
from __future__ import annotations

from .service import attach_span_attrs, traced


__all__ = ["attach_span_attrs", "traced"]
