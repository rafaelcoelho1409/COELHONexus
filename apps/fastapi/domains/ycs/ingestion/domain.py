"""ycs/ingestion — PURE Document → Qdrant payload projection.

Functional Core: takes a `langchain_core.documents.Document` whose
metadata was populated by `chunker.chunk_transcript`, projects the
fields the deprecated payload carries.

Direct port of deprecated `services/youtube/ingestion.py:L237-249`."""
from __future__ import annotations

from langchain_core.documents import Document


def build_payload(doc: Document) -> dict:
    """Project the Document's metadata into the Qdrant point payload.

    Source field shape (set in `chunker.chunk_transcript` + the
    metadata-cache pass in `service.ingest_to_qdrant`):
      video_id, chunk_index, total_chunks, lang, channel_id,
      title, channel, upload_date, webpage_url
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
    }


def build_chunk_metadata(
    lang: str,
    channel_id: str,
    title: str,
    channel: str,
    upload_date: str,
    webpage_url: str,
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
    }
