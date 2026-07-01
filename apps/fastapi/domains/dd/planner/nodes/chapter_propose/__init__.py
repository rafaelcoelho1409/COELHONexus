"""chapter_propose — proposes candidate chapters via a single long-context LLM call using structural seeds + distillates."""
from .node import chapter_propose
from .service import load_proposals


__all__ = ["chapter_propose", "load_proposals"]
