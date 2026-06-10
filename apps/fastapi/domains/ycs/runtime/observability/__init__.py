"""OTel span helpers for YCS RAG nodes. See service.py for the @traced
decorator + attach_span_attrs helper."""
from .service import attach_span_attrs, traced


__all__ = ["attach_span_attrs", "traced"]
