"""sawc_derive — MinIO key builders."""
from __future__ import annotations
from . import params



def sawc_latest_key(slug: str, chapter_id: str) -> str:
    return f"{params.BLOB_PREFIX}/{slug}/{chapter_id}/sawc-latest.json"


def derive_latest_key(slug: str, chapter_id: str) -> str:
    return f"{params.BLOB_PREFIX}/{slug}/{chapter_id}/sawc_derive-latest.json"
