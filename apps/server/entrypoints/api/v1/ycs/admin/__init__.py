"""ycs/admin — ES aggregations, library view, and Celery task-status helpers for FastHTML."""
from . import domain, service
from .router import router


__all__ = ["domain", "router", "service"]
