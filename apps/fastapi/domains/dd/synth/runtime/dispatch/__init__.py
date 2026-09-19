"""Synth dispatch — async runners shared by HTTP in-process + Celery worker."""
from . import service


__all__ = ["service"]
