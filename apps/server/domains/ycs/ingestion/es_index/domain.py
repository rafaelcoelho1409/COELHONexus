"""Deterministic transcript-to-search-passage projection."""
from __future__ import annotations

from typing import Any

import domains

from . import keys


def transcript_passages(
    transcript: dict[str, Any],
    chunker: Any,
) -> list[dict[str, Any]]:
    """Project one canonical transcript to the same chunks used by YCS Qdrant."""
    video_id = str(transcript.get("video_id") or "").strip()
    content = str(transcript.get("content") or "")
    if not video_id or not content.strip():
        return []
    lang = str(transcript.get("lang") or "en")
    transcript_id = str(transcript.get("id") or f"{video_id}_{lang}")
    metadata = {
        "transcript_id": transcript_id,
        "parent_video_id": str(
            transcript.get("parent_video_id")
            or domains.ycs.ingestion.domain.parent_video_id(video_id)
        ),
        "lang": lang,
        "channel_id": str(transcript.get("channel_id") or ""),
        "playlist_id": str(transcript.get("playlist_id") or ""),
        "part_index": transcript.get("part_index"),
        "part_total": transcript.get("part_total"),
        "content_hash": domains.ycs.ingestion.domain.content_hash(content),
        "chunking_policy_version": domains.ycs.ingestion.chunker.params.CHUNKING_POLICY_VERSION,
        "chunk_size_chars": domains.ycs.ingestion.chunker.params.CHUNK_SIZE_CHARS,
        "chunk_overlap_chars": domains.ycs.ingestion.chunker.params.CHUNK_OVERLAP_CHARS,
        "index_profile_fingerprint": domains.ycs.ingestion.domain.index_profile_fingerprint(),
    }
    chunks = domains.ycs.ingestion.chunker.domain.chunk_transcript(
        video_id = video_id,
        content = content,
        metadata = metadata,
        chunker = chunker,
    )
    return [
        {
            "_id": keys.passage_document_id(transcript_id, chunk.metadata["chunk_index"], chunk.page_content),
            "_source": {
                **chunk.metadata,
                "content": chunk.page_content,
            },
        }
        for chunk in chunks
    ]
