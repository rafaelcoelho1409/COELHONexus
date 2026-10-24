"""Redis / MinIO key builders for the LLM-usage counters — one layout for every namespace (`dd`, `ycs`)."""
from __future__ import annotations

from urllib.parse import quote


def counters_key(ns: str, run_id: str) -> str:
    """Run hash: `stage`, `total:<f>`, `node:<node>:<f>`, `errkind:<kind>`."""
    return f"coelhonexus:{ns}:{run_id}:llm:counters"


def models_key(ns: str, run_id: str, node_id: str) -> str:
    """Per-node model hash: `<model>:<f>`."""
    return f"coelhonexus:{ns}:{run_id}:llm:models:{node_id}"


def snapshot_key(ns: str, run_id: str) -> str:
    """MinIO object holding a finished run's usage payload."""
    return f"observability/{ns}/llm-counters/{quote(run_id, safe='')}.json"
