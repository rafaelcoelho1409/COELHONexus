"""Pure chunk projection shared by BD Elasticsearch and Qdrant writers."""
from __future__ import annotations
from . import params

from functools import lru_cache

from langchain_core.documents import Document
from langchain_text_splitters import RecursiveCharacterTextSplitter


@lru_cache(maxsize = 1)
def _create_passage_splitter() -> RecursiveCharacterTextSplitter:
    """Create and cache the splitter configured for the shared passage profile."""
    return RecursiveCharacterTextSplitter(
        chunk_size = params.CHUNK_SIZE,
        chunk_overlap = params.CHUNK_OVERLAP,
        add_start_index = True,
        separators = list(params.QDRANT_CHUNK_SEPARATORS),
    )


def page_passages(content: str) -> list[Document]:
    """Split one processed page using the exact profile shared with Qdrant."""
    if not isinstance(content, str) or not content.strip():
        return []
    return _create_passage_splitter().create_documents([content])
