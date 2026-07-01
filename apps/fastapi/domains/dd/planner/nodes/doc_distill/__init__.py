"""doc_distill — pass-through ≤80 docs; otherwise parallel LLM distillation so all distillates fit the chapter_propose long-context window."""
from .node import doc_distill
from .service import load_distillates


__all__ = ["doc_distill", "load_distillates"]
