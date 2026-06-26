"""ycs/chunker — PURE recursive-splitter wrappers.

Functional Core (`docs/CODE-CONVENTIONS.md` §4): no I/O, no async, no
clock. The `RecursiveCharacterTextSplitter` is itself synchronous +
deterministic; `chunk_transcript` is just a metadata projection over
its output.
"""
from __future__ import annotations

from langchain_core.documents import Document
from langchain_text_splitters import RecursiveCharacterTextSplitter

from .params import CHUNK_OVERLAP_CHARS, CHUNK_SIZE_CHARS, SEPARATORS


def create_chunker(
    chunk_size: int = CHUNK_SIZE_CHARS,
    chunk_overlap: int = CHUNK_OVERLAP_CHARS,
) -> RecursiveCharacterTextSplitter:
    """Returns a splitter configured with the deprecated separator
    ladder. Caller threads it through `chunk_transcript` per transcript
    rather than reinstantiating."""
    return RecursiveCharacterTextSplitter(
        chunk_size = chunk_size,
        chunk_overlap = chunk_overlap,
        separators = list(SEPARATORS),
        length_function = len,
    )


def chunk_transcript(
    video_id: str,
    content: str,
    metadata: dict,
    chunker: RecursiveCharacterTextSplitter,
) -> list[Document]:
    """Split + project. Each chunk lands as a Document with
    `(video_id, chunk_index, total_chunks)` plus the caller's metadata
    fields (title, channel, channel_id, upload_date, webpage_url, …)."""
    if not content or not content.strip():
        return []
    texts = chunker.split_text(content)
    return [
        Document(
            page_content = text,
            metadata = {
                "video_id":     video_id,
                "chunk_index":  i,
                "total_chunks": len(texts),
                **metadata,
            },
        )
        for i, text in enumerate(texts)
    ]
