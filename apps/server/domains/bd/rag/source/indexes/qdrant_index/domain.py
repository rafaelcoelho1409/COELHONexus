"""Stable identifiers for dense and sparse vector records."""
from __future__ import annotations
from . import params
from ... import chunking

import hashlib
import json
import re
import uuid


def index_profile() -> dict[str, object]:
    """Return the complete, versioned splitter and sparse-index contract."""
    return {
        "version": params.QDRANT_INDEX_PROFILE_VERSION,
        "chunking_policy_version": chunking.params.QDRANT_CHUNKING_VERSION,
        "chunk_size_chars": chunking.params.CHUNK_SIZE,
        "chunk_overlap_chars": chunking.params.CHUNK_OVERLAP,
        "chunk_separators": list(chunking.params.QDRANT_CHUNK_SEPARATORS),
        "sparse_model": params.SPARSE_MODEL_NAME,
    }


def index_profile_fingerprint() -> str:
    encoded = json.dumps(index_profile(), ensure_ascii = False, sort_keys = True, separators = (",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def collection_name(model: str, dimensions: int) -> str:
    safe_model = re.sub(r"[^a-z0-9]+", "_", model.lower()).strip("_")
    if not safe_model or dimensions < 1:
        raise ValueError("the embedding model and vector dimensions must be valid")
    return f"{params.QDRANT_COLLECTION_PREFIX}__{safe_model}__{dimensions}d"


def point_id(
    book_id: str,
    agentic_run_id: str,
    revision: int,
    page: int,
    chunk: int,
    text: str,
    profile_fingerprint: str | None = None,
) -> str:
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
    profile_segment = f":{profile_fingerprint}" if profile_fingerprint else ""
    return str(uuid.uuid5(
        uuid.NAMESPACE_URL,
        f"bd:{book_id}:{agentic_run_id}:{revision}:{page}:{chunk}:{digest}{profile_segment}",
    ))
