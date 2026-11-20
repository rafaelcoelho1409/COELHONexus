"""Configuration for bounded YCS graph extraction inputs."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen = True, slots = True)
class GraphChunkConfig:
    """Chunk and overflow-recovery limits for transcript graph extraction."""

    input_chars: int = 9_000
    overlap_chars: int = 240
    min_split_chars: int = 1_000
    max_split_depth: int = 8


GRAPH_CHUNK = GraphChunkConfig()
