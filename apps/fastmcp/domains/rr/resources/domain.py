"""RR resources — pure response shaping (Functional Core).

Per docs/CODE-CONVENTIONS.md §4: no I/O, no async, no logging. Same
inputs → same outputs. Error envelopes + input validation live here;
`service.py` owns the Postgres / MinIO / Neo4j calls.
"""
from __future__ import annotations
from . import params


def no_scan_payload() -> dict:
    """Envelope when no completed scan exists yet."""
    return {
        "error": "No completed scan found",
        "hint": "Run POST /api/v1/rr/scan from FastHTML first.",
    }


def missing_digest_payload(scan_id: str) -> dict:
    """Envelope when Postgres says done but MinIO lacks the artifact."""
    return {
        "error": "digest.json missing from MinIO",
        "scan_id": scan_id,
        "hint": "Postgres says done but MinIO doesn't have the artifact",
    }


def concept_not_found_payload(name: str) -> dict:
    """Envelope when Neo4j has no Concept node by that name."""
    return {
        "error": "concept not found in Neo4j",
        "name": name,
        "hint": "Run a scan that mentions this concept first.",
    }


def validate_concept_name(name: str) -> str | None:
    """Return an error string when `name` is unusable, else None.

    Neo4j MERGE was case-preserving, so matching is case-sensitive —
    but an empty or absurdly long input is never a real concept.
    """
    if not name or len(name) > params.CONCEPT_NAME_MAX_CHARS:
        return f"name must be 1-{params.CONCEPT_NAME_MAX_CHARS} chars"
    return None
