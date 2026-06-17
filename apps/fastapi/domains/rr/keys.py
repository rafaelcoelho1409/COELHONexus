"""Identifier registries for the RR domain.

Per docs/CODE-CONVENTIONS.md §2: identifier constants (source names,
table names, collection name, MinIO key builders, scan statuses) live
here — not as inlined literals scattered through domain.py / stores.

Rename safety: change here once → propagates everywhere downstream.
"""
from __future__ import annotations


# --------------------------------------------------------------------------- #
# Source provenance identifiers
# --------------------------------------------------------------------------- #
# Match `Paper.source` / `Hit.source` of each MCP tool's boundary schema in
# apps/fastmcp/domains/rr/tools/<source>/schemas.py EXACTLY (typos here =
# silent dedup fails).
SOURCE_ARXIV: str = "arxiv"
SOURCE_S2:    str = "semantic_scholar"
SOURCE_HF:    str = "huggingface_daily_papers"
SOURCE_HN:    str = "hn"

SOURCES_ALL: tuple[str, ...] = (SOURCE_ARXIV, SOURCE_S2, SOURCE_HF, SOURCE_HN)


# S2's external_ids dict carries cross-source IDs. The arxiv key is "ArXiv"
# (case-sensitive per S2 API). Used by normalize_s2 → dedup_by_arxiv_id.
S2_EXTERNAL_ID_ARXIV: str = "ArXiv"


# --------------------------------------------------------------------------- #
# Postgres tables (architecture doc §2.4.3)
# --------------------------------------------------------------------------- #
PG_TABLE_SCANS:    str = "radar_scans"
PG_TABLE_FINDINGS: str = "radar_findings"
PG_TABLE_SEEN:     str = "radar_seen"
PG_TABLE_PROFILES: str = "radar_profiles"


# Scan status state machine. `pending` → `running` → (`done` | `error` |
# `cancelled`). Stored as TEXT in radar_scans.status; the FastHTML page
# colors digest cards based on these.
SCAN_STATUS_PENDING:   str = "pending"
SCAN_STATUS_RUNNING:   str = "running"
SCAN_STATUS_DONE:      str = "done"
SCAN_STATUS_ERROR:     str = "error"
SCAN_STATUS_CANCELLED: str = "cancelled"


# --------------------------------------------------------------------------- #
# Qdrant
# --------------------------------------------------------------------------- #
# Collection name for the radar's paper-abstract vectors. One collection per
# domain — DD has its own, YCS has its own.
QDRANT_COLLECTION: str = "radar_papers"

# Payload-index field names — must match upsert_paper_vector's payload dict
# keys exactly. Used both at bootstrap (create_payload_index) and at search
# time (filter on these).
QDRANT_PAYLOAD_ARXIV_ID:  str = "arxiv_id"
QDRANT_PAYLOAD_SIGNAL:    str = "signal"
QDRANT_PAYLOAD_PUBLISHED: str = "published"
QDRANT_PAYLOAD_SOURCES:   str = "sources"


# --------------------------------------------------------------------------- #
# MinIO — shared bucket, RR keys prefixed `rr/`
# --------------------------------------------------------------------------- #
# All RR artifacts share the existing `coelhonexus` bucket (env var
# MINIO_BUCKET_COELHONEXUS). Prefixes namespace the radar's content from
# dd/ and ycs/ artifacts.
MINIO_PREFIX_RR:    str = "rr"
MINIO_PREFIX_SCANS: str = "rr/scans"

# MIME used for the JSON artifacts (digest + per-paper extractions).
MINIO_JSON_CONTENT_TYPE: str = "application/json"


def digest_minio_key(scan_id: str) -> str:
    """Final digest snapshot — written by the `report` subagent at scan end.
    Survives Postgres deletion (the operator can re-render a digest from
    MinIO even if radar_findings is truncated)."""
    return f"{MINIO_PREFIX_SCANS}/{scan_id}/digest.json"


def extraction_minio_key(scan_id: str, arxiv_id: str) -> str:
    """Per-paper deep-read output — one file per arxiv_id. Step 4 writes
    these; the synthesis subagent reads them from state.fs but the
    canonical artifact lives here for cross-scan re-use."""
    return f"{MINIO_PREFIX_SCANS}/{scan_id}/extractions/{arxiv_id}.json"


def code_minio_key(scan_id: str, arxiv_id: str, prompt_version: str) -> str:
    """Build-tab synthesized Python — one file per (scan, arxiv_id, prompt
    version). Written lazily on first GET /code request, never during the
    deep_read phase (most papers never have their Build tab opened —
    pre-computing would waste rotator budget).

    Versioning: the prompt_version segment lets multiple prompt revisions
    coexist for the same paper; the operator can wipe the whole `code/`
    dir to GC. Keyed as a directory (not a single object) so a single
    scan's code artifacts share a prefix for batch delete."""
    return (
        f"{MINIO_PREFIX_SCANS}/{scan_id}/code/"
        f"{arxiv_id}_{prompt_version}.py"
    )


# Plain-text MIME for the .py artifacts the Build tab persists.
MINIO_PYTHON_CONTENT_TYPE: str = "text/x-python"


# --------------------------------------------------------------------------- #
# Neo4j — label + relationship names (architecture doc §2.4.1)
# --------------------------------------------------------------------------- #
NEO4J_LABEL_PAPER:   str = "Paper"
NEO4J_LABEL_AUTHOR:  str = "Author"
NEO4J_LABEL_CONCEPT: str = "Concept"
NEO4J_LABEL_SOURCE:  str = "Source"

NEO4J_REL_CITES:    str = "CITES"
NEO4J_REL_AUTHORED: str = "AUTHORED"
NEO4J_REL_ABOUT:    str = "ABOUT"
NEO4J_REL_FROM:     str = "FROM"
