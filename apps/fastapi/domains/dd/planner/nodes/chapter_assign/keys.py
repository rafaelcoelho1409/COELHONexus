"""chapter_assign — MinIO key builders."""
from __future__ import annotations
from . import params



def versioned_key(slug: str, manifest: str) -> str:
    return f"{params.BLOB_PREFIX}/{slug}/chapter_assign/{manifest}.json"


def latest_key(slug: str) -> str:
    return f"{params.BLOB_PREFIX}/{slug}/chapter_assign-latest.json"
