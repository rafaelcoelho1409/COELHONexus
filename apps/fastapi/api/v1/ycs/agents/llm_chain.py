"""ycs/agents — LLM chain factory. Delegates to the rotator's `build_llm_fallback_chain()` so Ask
inherits FGTS-VA bandit, EOL detection, and catalog refresh without a per-consumer fallback list."""
from __future__ import annotations

from domains.llm.rotator.chain import build_llm_fallback_chain


def build_deprecated_llm_chain():
    """Backward-compat name kept for `app.py` lifespan importers."""
    return build_llm_fallback_chain()
