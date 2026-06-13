"""POST/GET /v1/rr/scan + SSE — driven by the Celery `run_radar_scan` task."""
from .router import router


__all__ = ["router"]
