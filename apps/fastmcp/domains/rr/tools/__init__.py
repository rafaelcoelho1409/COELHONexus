"""Research Radar MCP tools — one sub-package per source (arxiv, openalex,
semantic_scholar, hn, …). Each follows the conventions-compliant layout:
`tool.py` (boundary) · `service.py` (I/O) · `domain.py` (pure) ·
`schemas.py` (Pydantic) · `params.py` (frozen-dataclass config).
"""
from __future__ import annotations
from . import arxiv, hn, huggingface_daily_papers, openalex, semantic_scholar


__all__ = [
    "arxiv",
    "hn",
    "huggingface_daily_papers",
    "openalex",
    "semantic_scholar",
]
