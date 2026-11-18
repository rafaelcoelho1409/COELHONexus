"""Pure chunk projection shared by BD Elasticsearch and Qdrant writers."""
from __future__ import annotations

from langchain_core.documents import Document
from langchain_text_splitters import RecursiveCharacterTextSplitter

from .. import params


_SPLITTER = RecursiveCharacterTextSplitter(
    chunk_size = params.CHUNK_SIZE,
    chunk_overlap = params.CHUNK_OVERLAP,
    add_start_index = True,
    separators = list(params.QDRANT_CHUNK_SEPARATORS),
)


def page_passages(content: str) -> list[Document]:
    """Split one processed page using the exact profile shared with Qdrant."""
    if not isinstance(content, str) or not content.strip():
        return []
    return _SPLITTER.create_documents([content])
