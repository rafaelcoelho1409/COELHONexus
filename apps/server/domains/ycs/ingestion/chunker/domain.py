"""ycs/ingestion/chunker — PURE recursive-splitter wrappers.

Functional Core (`docs/CODE-CONVENTIONS.md` §4): no I/O, no async, no
clock. The `RecursiveCharacterTextSplitter` is itself synchronous +
deterministic; `chunk_transcript` is just a metadata projection over
its output.
"""
from __future__ import annotations
from . import params

from langchain_core.documents import Document
from langchain_text_splitters import RecursiveCharacterTextSplitter


def create_chunker(
    chunk_size: int = params.CHUNK_SIZE_CHARS,
    chunk_overlap: int = params.CHUNK_OVERLAP_CHARS,
) -> RecursiveCharacterTextSplitter:
    """Returns a splitter configured with the deprecated separator
    ladder. Caller threads it through `chunk_transcript` per transcript
    rather than reinstantiating."""
    return RecursiveCharacterTextSplitter(
        chunk_size = chunk_size,
        chunk_overlap = chunk_overlap,
        separators = list(params.SEPARATORS),
        length_function = len,
        add_start_index = True,
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
    documents = chunker.create_documents([content])
    return [
        Document(
            page_content = document.page_content,
            metadata = {
                "video_id":     video_id,
                "chunk_index":  i,
                "total_chunks": len(documents),
                "source_char_start": int(document.metadata.get("start_index", 0)),
                "source_char_end": int(document.metadata.get("start_index", 0)) + len(document.page_content),
                **metadata,
            },
        )
        for i, document in enumerate(documents)
    ]
