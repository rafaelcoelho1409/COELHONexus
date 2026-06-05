"""Ingestion entry point: resolver → tier dispatch → post-process → finalize."""
from .service import run


__all__ = ["run"]
