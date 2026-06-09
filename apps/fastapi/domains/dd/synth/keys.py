"""Identifier + key-builder surface shared across the synth package.

Redis namespaces:
  dd:synth:{thread_id}:cancel             → per-thread cancel flag
  dd:synth:{thread_id}:events             → pub/sub event channel
  dd:synth:{thread_id}:events:snapshot    → catch-up replay list
  dd:synth:lock:{slug}                    → single-flight start lock
  dd:study:current:{slug}                 → live-study registry

MinIO keys:
  synth/{slug}/study-timing-latest.json
  synth/{slug}/{chapter_id}/README.md
  synth/{slug}/{chapter_id}/render-latest.json
  synth/{slug}/book_harmonize/{manifest_hash}.json
  synth/{slug}/book_harmonize-latest.json
"""
from __future__ import annotations

import os


def redis_url() -> str:
    """Build the Redis URL from env. Strict reads — secrets must be set."""
    host = os.environ["REDIS_HOST"]
    port = os.environ["REDIS_PORT"]
    password = os.environ["REDIS_PASSWORD"]
    return (
        f"redis://:{password}@{host}:{port}"
        if password else f"redis://{host}:{port}"
    )


def cancel_key(thread_id: str) -> str:
    return f"dd:synth:{thread_id}:cancel"


def event_channel(thread_id: str) -> str:
    return f"dd:synth:{thread_id}:events"


def snapshot_key(thread_id: str) -> str:
    return f"dd:synth:{thread_id}:events:snapshot"


def lock_key(slug: str) -> str:
    return f"dd:synth:lock:{slug}"


def active_study_key(slug: str) -> str:
    return f"dd:study:current:{slug}"


def study_timing_key(slug: str) -> str:
    """MinIO key for the persisted study timing roll-up — survives a UI
    refresh so totals show for finished or cached studies."""
    return f"synth/{slug}/study-timing-latest.json"


def chapter_readme_key(slug: str, chapter_id: str) -> str:
    return f"synth/{slug}/{chapter_id}/README.md"


def chapter_render_latest_key(slug: str, chapter_id: str) -> str:
    return f"synth/{slug}/{chapter_id}/render-latest.json"


def book_harmonize_versioned_key(slug: str, manifest_hash: str) -> str:
    return f"synth/{slug}/book_harmonize/{manifest_hash}.json"


def book_harmonize_latest_key(slug: str) -> str:
    return f"synth/{slug}/book_harmonize-latest.json"
