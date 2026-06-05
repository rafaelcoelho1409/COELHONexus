"""sawc_derive — MinIO key builders."""
from __future__ import annotations

from .params import BLOB_PREFIX


def sawc_latest_key(slug: str, chapter_id: str) -> str:
    return f"{BLOB_PREFIX}/{slug}/{chapter_id}/sawc-latest.json"


def derive_latest_key(slug: str, chapter_id: str) -> str:
    return f"{BLOB_PREFIX}/{slug}/{chapter_id}/sawc_derive-latest.json"


def ingestion_source_key(slug: str, basename: str) -> str:
    """Mirrors the pattern the rest of synth uses to read raw ingestion
    pages."""
    return f"ingestion/{slug}/pages/{basename}"
