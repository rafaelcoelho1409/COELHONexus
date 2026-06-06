"""ycs/reranker — FlashRank cross-encoder reranking.

Direct port of deprecated `services/youtube/reranker.py`."""
from .params import DEFAULT_TOP_K, PER_DOC_CHAR_CAP
from .service import rerank_documents


__all__ = [
    "DEFAULT_TOP_K",
    "PER_DOC_CHAR_CAP",
    "rerank_documents",
]
