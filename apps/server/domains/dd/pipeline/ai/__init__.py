"""The planner's run: composition root over `ai.dd.graphs.pipeline.planner`. `task.py` (Celery entry points) is deliberately NOT re-exported
here — it imports `infra.celery`, which reads strict env vars (REDIS_HOST/ENVIRONMENT/...) at import time, and forcing that onto every
bare `import domains` would break local and standalone use. Callers that need it use a direct `from domains.dd.pipeline.ai.task import X`."""
from __future__ import annotations

from . import service

__all__ = ["service"]
