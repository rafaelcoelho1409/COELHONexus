"""Dataclasses, type aliases, and exception classes for storage."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


ContentType = Literal[
    "text/markdown",
    "application/json",
    "text/plain",
    "application/octet-stream",
]


@dataclass
class ManifestEntry:
    idx: int
    slug: str
    url: str
    tier: str
    bytes: int
    title: str = ""
    key: str = ""        # MinIO key — present once written
