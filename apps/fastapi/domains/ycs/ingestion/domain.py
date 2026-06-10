"""ycs/ingestion — PURE Document → Qdrant payload projection +
content fingerprint.

Functional Core: takes a `langchain_core.documents.Document` whose
metadata was populated by `chunker.chunk_transcript`, projects the
fields the deprecated payload carries.

Direct port of deprecated `services/youtube/ingestion.py:L237-249`,
extended 2026-06-10 with `content_hash` (re-ingest skip fingerprint)."""
from __future__ import annotations

import hashlib

from langchain_core.documents import Document


def content_hash(content: str) -> str:
    """Fingerprint of a transcript's full text. Stored on every Qdrant
    point of the video; an unchanged hash on re-ingest means the
    video's chunks are already current and embedding can be skipped
    entirely (the expensive stage — ~11 s per 50-chunk NIM call)."""
    return hashlib.md5((content or "").encode("utf-8")).hexdigest()


def build_payload(doc: Document) -> dict:
    """Project the Document's metadata into the Qdrant point payload.

    Source field shape (set in `chunker.chunk_transcript` + the
    metadata-cache pass in `service.ingest_to_qdrant`):
      video_id, chunk_index, total_chunks, lang, channel_id,
      title, channel, upload_date, webpage_url, content_hash
    """
    md = doc.metadata
    return {
        "content":       doc.page_content,
        "video_id":      md["video_id"],
        "chunk_index":   md["chunk_index"],
        "total_chunks":  md["total_chunks"],
        "title":         md.get("title", ""),
        "channel":       md.get("channel", ""),
        "channel_id":    md.get("channel_id", ""),
        "lang":          md.get("lang", "en"),
        "upload_date":   md.get("upload_date", ""),
        "webpage_url":   md.get("webpage_url", ""),
        "content_hash":  md.get("content_hash", ""),
    }


def build_chunk_metadata(
    lang: str,
    channel_id: str,
    title: str,
    channel: str,
    upload_date: str,
    webpage_url: str,
    content_hash: str = "",
) -> dict:
    """Metadata dict fed to `chunker.chunk_transcript` for each
    transcript. Centralizing this projection keeps the field list in
    one place (matches the payload projection above 1:1)."""
    return {
        "lang":         lang,
        "channel_id":   channel_id,
        "title":        title,
        "channel":      channel,
        "upload_date":  upload_date,
        "webpage_url":  webpage_url,
        "content_hash": content_hash,
    }
