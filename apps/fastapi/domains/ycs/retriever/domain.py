"""ycs/retriever — PURE dedup helper shared by the orchestrator.

Functional Core (`docs/CODE-CONVENTIONS.md` §4): no I/O, no async. The
`SmartRetriever` uses this to merge the three arms before reranking.

Mirror of deprecated `services/youtube/retriever.py:L498-513`."""
from __future__ import annotations

from langchain_core.documents import Document


def dedupe_documents(documents: list[Document]) -> list[Document]:
    """Drop duplicate `(video_id, chunk_index, content[:100])` tuples
    while preserving first-encounter order.

    Why a 100-char content prefix in the key: video_id+chunk_index alone
    isn't unique when one arm (Neo4j graph) returns synthetic content
    like `"Dubai (DISCUSSES Cryptocurrency)"` — those don't have
    chunk_indexes. The content prefix disambiguates."""
    seen: set[tuple[str, int | str, str]] = set()
    unique: list[Document] = []
    for doc in documents:
        key = (
            doc.metadata.get("video_id", ""),
            doc.metadata.get("chunk_index", ""),
            doc.page_content[:100],
        )
        if key in seen:
            continue
        seen.add(key)
        unique.append(doc)
    return unique
