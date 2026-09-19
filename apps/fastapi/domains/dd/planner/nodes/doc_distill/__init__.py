"""doc_distill — pass-through ≤80 docs; otherwise parallel LLM distillation so all distillates fit the chapter_propose long-context window."""
from . import node, service


__all__ = ["node", "service"]
