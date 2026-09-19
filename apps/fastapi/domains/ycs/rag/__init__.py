"""ycs/rag — LangGraph RAG workflows (standard pipeline + adaptive parent).
Shared pure helpers (`domain.py`), resilient-call + web-search-fallback
I/O (`service.py`), and their tunables/patterns live at this level
because both `standard/` and `adaptive/` call into them."""
from __future__ import annotations

from . import adaptive, domain, params, patterns, service, standard
