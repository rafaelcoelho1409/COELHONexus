"""ycs/ingestion — PURE Document → Qdrant payload projection +
content fingerprint.

Functional Core: takes a `langchain_core.documents.Document` whose
metadata was populated by `chunker.chunk_transcript`, projects the
fields the Qdrant point payload carries.
"""
from __future__ import annotations

import hashlib
import json
import re

from . import params
from .chunker import params as chunker_params
from langchain_core.documents import Document


def index_profile_fingerprint(
    chunk_size_chars: int = chunker_params.CHUNK_SIZE_CHARS,
    chunk_overlap_chars: int = chunker_params.CHUNK_OVERLAP_CHARS,
) -> str:
    profile = {
        "version": params.QDRANT_INDEX_PROFILE_VERSION,
        "chunking_policy_version": chunker_params.CHUNKING_POLICY_VERSION,
        "chunk_size_chars": chunk_size_chars,
        "chunk_overlap_chars": chunk_overlap_chars,
        "chunk_separators": list(chunker_params.SEPARATORS),
        "sparse_model": params.QDRANT_SPARSE_MODEL_NAME,
    }
    encoded = json.dumps(profile, ensure_ascii = False, sort_keys = True, separators = (",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def physical_collection_name(base: str, embedding_model: str, dimensions: int) -> str:
    """Return the deterministic physical collection for an embedding space.

    The model identifier is normalized for Qdrant collection names, so
    each model and vector dimension pair has its own isolated collection.
    """
    safe_model = re.sub(r"[^a-z0-9]+", "_", embedding_model.lower()).strip("_")
    if not safe_model or dimensions < 1:
        raise ValueError("the embedding model and vector dimensions must be valid")
    return f"{base}__{safe_model}__{dimensions}d"


def parent_video_id(vid: str) -> str:
    """Strip a long-video partition suffix (`"XYZ#p3"` -> `"XYZ"`).
    Identity for any id that isn't a partition. `INDEX_METADATA` (yt-dlp
    title/channel/upload_date/webpage_url) is only ever written once per
    real YouTube video — a partition id has no entry of its own, so any
    metadata lookup keyed on the raw partition id would silently come
    back empty. Centralized here (used by `fetch_metadata_from_es`)
    rather than patched at each of its 3 call sites."""
    base, sep, rest = vid.partition("#p")
    return base if base and sep and rest.isdigit() else vid


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
      title, channel, upload_date, webpage_url, content_hash, and the
      embedding/chunking profile for reproducible retrieval rebuilds.
    """
    md = doc.metadata
    return {
        "content":       doc.page_content,
        "video_id":      md["video_id"],
        "chunk_index":   md["chunk_index"],
        "total_chunks":  md["total_chunks"],
        "source_char_start": md.get("source_char_start", 0),
        "source_char_end": md.get("source_char_end", 0),
        "title":         md.get("title", ""),
        "channel":       md.get("channel", ""),
        "channel_id":    md.get("channel_id", ""),
        "lang":          md.get("lang", "en"),
        "upload_date":   md.get("upload_date", ""),
        "webpage_url":   md.get("webpage_url", ""),
        "content_hash":  md.get("content_hash", ""),
        "embedding_model": md.get("embedding_model", ""),
        "embedding_dimensions": md.get("embedding_dimensions", 0),
        "index_profile_version": md.get("index_profile_version", params.QDRANT_INDEX_PROFILE_VERSION),
        "chunking_policy_version": md.get("chunking_policy_version", chunker_params.CHUNKING_POLICY_VERSION),
        "chunk_size_chars": md.get("chunk_size_chars", chunker_params.CHUNK_SIZE_CHARS),
        "chunk_overlap_chars": md.get("chunk_overlap_chars", chunker_params.CHUNK_OVERLAP_CHARS),
        "chunk_separators": md.get("chunk_separators", list(chunker_params.SEPARATORS)),
        "sparse_model": md.get("sparse_model", params.QDRANT_SPARSE_MODEL_NAME),
        "index_profile_fingerprint": md.get(
            "index_profile_fingerprint",
            index_profile_fingerprint(
                md.get("chunk_size_chars", chunker_params.CHUNK_SIZE_CHARS),
                md.get("chunk_overlap_chars", chunker_params.CHUNK_OVERLAP_CHARS),
            ),
        ),
    }


def build_chunk_metadata(
    lang: str,
    channel_id: str,
    title: str,
    channel: str,
    upload_date: str,
    webpage_url: str,
    content_hash: str = "",
    chunk_size_chars: int = chunker_params.CHUNK_SIZE_CHARS,
    chunk_overlap_chars: int = chunker_params.CHUNK_OVERLAP_CHARS,
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
        "index_profile_version": params.QDRANT_INDEX_PROFILE_VERSION,
        "chunking_policy_version": chunker_params.CHUNKING_POLICY_VERSION,
        "chunk_size_chars": chunk_size_chars,
        "chunk_overlap_chars": chunk_overlap_chars,
        "chunk_separators": list(chunker_params.SEPARATORS),
        "sparse_model": params.QDRANT_SPARSE_MODEL_NAME,
        "index_profile_fingerprint": index_profile_fingerprint(chunk_size_chars, chunk_overlap_chars),
    }
