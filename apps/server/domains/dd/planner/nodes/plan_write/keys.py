"""plan_write — MinIO key builders."""
from __future__ import annotations
import domains
from . import params



def versioned_blob_key(slug: str, manifest_hash: str) -> str:
    return f"{params.BLOB_PREFIX}/{slug}/plan/{manifest_hash}.json"


def latest_blob_key(slug: str) -> str:
    """The latest-plan pointer this node writes — the same key synth and the
    API read (canonical builder: domains.dd.planner.keys)."""
    return domains.dd.planner.keys.plan_latest_key(slug)
