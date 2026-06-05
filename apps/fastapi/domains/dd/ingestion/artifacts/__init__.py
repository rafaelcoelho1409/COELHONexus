"""Per-page artifact extraction — download media references at ingest time +
rewrite URLs to point at our MinIO copies. See service.py docstrings."""
from __future__ import annotations

from .entities import Artifact
from .keys import public_artifact_path
from .service import (
    extract_and_save_artifacts,
    extract_and_save_artifacts_from_md,
)

__all__ = [
    "Artifact",
    "extract_and_save_artifacts",
    "extract_and_save_artifacts_from_md",
    "public_artifact_path",
]
