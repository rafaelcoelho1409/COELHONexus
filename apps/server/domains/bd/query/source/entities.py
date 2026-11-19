"""Validated request structures for BD Query."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen = True, slots = True)
class ParsedElasticsearchBody:
    body: dict[str, Any]
    synth_size: bool


@dataclass(frozen = True, slots = True)
class ParsedQdrantOperation:
    operation: str
    body: dict[str, Any]
