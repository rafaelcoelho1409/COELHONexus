"""Store-specific I/O for the RR domain — Imperative Shell.

Per docs/CODE-CONVENTIONS.md §8 strict-merge: MinIO / Neo4j / Postgres /
Qdrant each have a different driver setup (aioboto3 / neo4j / psycopg /
qdrant_client), but none is a feature name distinct from "this domain's
persistence layer" — same role (I/O adapter for one backend), same file,
sectioned below. `service.py` (this file) composes them; the orchestrator
in `../service.py` composes THIS module's functions into scan-level
operations.

  MinIO     digest.json + per-paper extraction.json + Build-tab code.py artifacts
  Neo4j     Paper / Author / Concept / Source graph (MERGE by arxiv_id)
  Postgres  radar_scans · findings · seen · profiles (relational state)
  Qdrant    radar_papers vector collection + payload index ops
"""
from __future__ import annotations

import json
import logging
import os
from typing import Any, Optional
from uuid import NAMESPACE_URL, UUID, uuid5

import aioboto3
import domains, infra
import psycopg
from botocore.config import Config
from botocore.exceptions import ClientError
from neo4j import AsyncDriver
from psycopg.types.json import Jsonb
from qdrant_client.http.models import (
    Distance,
    FieldCondition,
    Filter,
    MatchValue,
    OptimizersConfigDiff,
    PayloadSchemaType,
    PointStruct,
    VectorParams,
)


from .. import entities, keys, params


logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# MinIO — digest.json + per-paper extraction.json artifacts
# ---------------------------------------------------------------------------

# Session / config — one Session per process; clients are per-operation
_minio_session: Optional[aioboto3.Session] = None
_MINIO_BOTO_CONFIG = Config(
    signature_version    = "s3v4",      # MinIO requires v4; default v2 fails
    max_pool_connections = 16,
    connect_timeout      = 5.0,
    read_timeout         = 30.0,
    retries              = {"max_attempts": 3, "mode": "standard"},
)


def _minio_get_session() -> aioboto3.Session:
    global _minio_session
    if _minio_session is None:
        _minio_session = aioboto3.Session()
    return _minio_session


def _minio_bucket() -> str:
    """Resolve the bucket name from env (set in Helm values.yaml +
    propagated via the fastapi configmap)."""
    return os.environ["MINIO_BUCKET_COELHONEXUS"]


def _minio_endpoint() -> str:
    return os.environ["MINIO_ENDPOINT"].strip()


def _minio_client():
    """An aioboto3 s3 async-context-manager client. Use:

        async with _minio_client() as s3:
            await s3.put_object(...)
    """
    return _minio_get_session().client(
        "s3",
        endpoint_url          = _minio_endpoint(),
        aws_access_key_id     = os.environ["AWS_ACCESS_KEY_ID"],
        aws_secret_access_key = os.environ["AWS_SECRET_ACCESS_KEY"],
        region_name           = "us-east-1",
        config                = _MINIO_BOTO_CONFIG,
    )


async def bootstrap_minio() -> None:
    """head_bucket; create_bucket on 404. Same pattern as the dd ingestion
    storage's ensure_bucket()."""
    bucket = _minio_bucket()
    async with _minio_client() as s3:
        try:
            await s3.head_bucket(Bucket=bucket)
            logger.info(
                f"[rr-minio] bucket {bucket!r} exists "
                f"(prefix={keys.MINIO_PREFIX_RR!r})"
            )
            return
        except ClientError as e:
            code = (e.response or {}).get("Error", {}).get("Code", "")
            if code not in ("404", "NoSuchBucket", "NoSuchKey"):
                raise
        await s3.create_bucket(Bucket=bucket)
        logger.info(f"[rr-minio] created bucket {bucket!r}")


async def put_digest_json(scan_id: str, payload: dict[str, Any]) -> str:
    """Write the scan's digest snapshot. Returns the MinIO key."""
    key  = keys.digest_minio_key(scan_id)
    body = json.dumps(payload, default=str).encode("utf-8")
    async with _minio_client() as s3:
        await s3.put_object(
            Bucket      = _minio_bucket(),
            Key         = key,
            Body        = body,
            ContentType = params.STORES_PARAMS.minio_json_content_type,
        )
    return key


async def get_digest_json(scan_id: str) -> dict[str, Any] | None:
    """Read the digest snapshot. Returns None on 404."""
    key = keys.digest_minio_key(scan_id)
    async with _minio_client() as s3:
        try:
            obj = await s3.get_object(Bucket=_minio_bucket(), Key=key)
        except ClientError as e:
            code = (e.response or {}).get("Error", {}).get("Code", "")
            if code in ("404", "NoSuchKey"):
                return None
            raise
        body = await obj["Body"].read()
    return json.loads(body)


async def delete_digest_json(scan_id: str) -> bool:
    """Remove the digest object for one scan. Idempotent — returns True if
    the object was present, False if it wasn't. Other errors raise.

    Caller: `../service.py::delete_scan` (the per-row delete affordance in
    the Recent-scans dropdown)."""
    key = keys.digest_minio_key(scan_id)
    async with _minio_client() as s3:
        try:
            await s3.delete_object(Bucket=_minio_bucket(), Key=key)
            return True
        except ClientError as e:
            code = (e.response or {}).get("Error", {}).get("Code", "")
            if code in ("404", "NoSuchKey"):
                return False
            raise


async def put_extraction_json(
    scan_id: str, arxiv_id: str, payload: dict[str, Any]
) -> str:
    """Write a deep_read extraction for one paper. Returns the MinIO key."""
    key  = keys.extraction_minio_key(scan_id, arxiv_id)
    body = json.dumps(payload, default=str).encode("utf-8")
    async with _minio_client() as s3:
        await s3.put_object(
            Bucket      = _minio_bucket(),
            Key         = key,
            Body        = body,
            ContentType = params.STORES_PARAMS.minio_json_content_type,
        )
    return key


async def get_extraction_json(
    scan_id: str, arxiv_id: str,
) -> dict[str, Any] | None:
    """Read an extraction. Returns None on 404."""
    key = keys.extraction_minio_key(scan_id, arxiv_id)
    async with _minio_client() as s3:
        try:
            obj = await s3.get_object(Bucket=_minio_bucket(), Key=key)
        except ClientError as e:
            code = (e.response or {}).get("Error", {}).get("Code", "")
            if code in ("404", "NoSuchKey"):
                return None
            raise
        body = await obj["Body"].read()
    return json.loads(body)


async def put_code_py(
    scan_id: str, arxiv_id: str, prompt_version: str, code: str,
) -> str:
    """Persist a synthesized Python file. Returns the MinIO key.
    Content-Type is `text/x-python` so an operator browsing MinIO sees it
    rendered as plain text instead of being treated as JSON."""
    key  = keys.code_minio_key(scan_id, arxiv_id, prompt_version)
    body = code.encode("utf-8")
    async with _minio_client() as s3:
        await s3.put_object(
            Bucket      = _minio_bucket(),
            Key         = key,
            Body        = body,
            ContentType = keys.MINIO_PYTHON_CONTENT_TYPE,
        )
    return key


async def get_code_py(
    scan_id: str, arxiv_id: str, prompt_version: str,
) -> str | None:
    """Read a synthesized Python file. Returns None on 404 (i.e. the Build
    tab has never been opened for this paper at this prompt version, or
    the cache was wiped)."""
    key = keys.code_minio_key(scan_id, arxiv_id, prompt_version)
    async with _minio_client() as s3:
        try:
            obj = await s3.get_object(Bucket=_minio_bucket(), Key=key)
        except ClientError as e:
            code = (e.response or {}).get("Error", {}).get("Code", "")
            if code in ("404", "NoSuchKey"):
                return None
            raise
        body = await obj["Body"].read()
    return body.decode("utf-8")


async def delete_code_dir(scan_id: str) -> int:
    """Drop every Build-tab artifact for one scan (all arxiv_ids, all
    prompt versions). Idempotent — returns the count of objects deleted.
    Called by `../service.py::delete_scan` so the Recent-scans dropdown's
    delete button doesn't leak code blobs."""
    prefix = f"{keys.MINIO_PREFIX_SCANS}/{scan_id}/code/"
    deleted = 0
    async with _minio_client() as s3:
        continuation: str | None = None
        while True:
            kwargs: dict[str, Any] = {"Bucket": _minio_bucket(), "Prefix": prefix}
            if continuation:
                kwargs["ContinuationToken"] = continuation
            page = await s3.list_objects_v2(**kwargs)
            objs = page.get("Contents") or []
            if not objs:
                break
            await s3.delete_objects(
                Bucket = _minio_bucket(),
                Delete = {"Objects": [{"Key": o["Key"]} for o in objs]},
            )
            deleted += len(objs)
            if not page.get("IsTruncated"):
                break
            continuation = page.get("NextContinuationToken")
    return deleted


# ---------------------------------------------------------------------------
# Neo4j — Paper · Author · Concept · Source graph
# ---------------------------------------------------------------------------

# Bootstrap — constraints + indexes. Idempotent (IF NOT EXISTS on every
# statement). Run once at startup; safe to re-run.
_NEO4J_BOOTSTRAP_STMTS: tuple[str, ...] = (
    # Uniqueness — guarantees MERGE-by-id is O(1)
    f"CREATE CONSTRAINT paper_id_unique IF NOT EXISTS "
    f"FOR (p:{keys.NEO4J_LABEL_PAPER}) REQUIRE p.id IS UNIQUE",
    f"CREATE CONSTRAINT concept_name_unique IF NOT EXISTS "
    f"FOR (c:{keys.NEO4J_LABEL_CONCEPT}) REQUIRE c.name IS UNIQUE",
    f"CREATE CONSTRAINT source_name_unique IF NOT EXISTS "
    f"FOR (s:{keys.NEO4J_LABEL_SOURCE}) REQUIRE s.name IS UNIQUE",
    # Indexes — payoff for ORDER BY / WHERE clauses used by the synthesis
    # subagent's GraphRAG queries and the digest renderer's top-N pulls.
    f"CREATE INDEX paper_signal_idx IF NOT EXISTS "
    f"FOR (p:{keys.NEO4J_LABEL_PAPER}) ON (p.signal)",
    f"CREATE INDEX paper_published_idx IF NOT EXISTS "
    f"FOR (p:{keys.NEO4J_LABEL_PAPER}) ON (p.published)",
)


async def bootstrap_neo4j() -> None:
    """Create constraints + indexes if missing. Idempotent."""
    driver: AsyncDriver = infra.neo4j.service.get_driver()
    async with driver.session(database=infra.neo4j.params.NEO4J_DATABASE) as session:
        for stmt in _NEO4J_BOOTSTRAP_STMTS:
            await session.run(stmt)
    logger.info(
        f"[rr-neo4j] bootstrap complete "
        f"({len(_NEO4J_BOOTSTRAP_STMTS)} statements, db={infra.neo4j.params.NEO4J_DATABASE!r})"
    )


# Paper upsert — MERGE by arxiv_id; sources / authors / concepts grafted
# onto the same node so cross-source ingest collapses correctly.
#
# 2026-09-17: every node gets `:COELHONexus:RR` stamped on via `SET
# n:{PROJECT_LABEL}:{SOURCE_LABEL}` — mirrors YCS's `:COELHONexus:YCS`
# tagging (`domains/ycs/graph_builder/service.py`) so the shared Neo4j
# instance can tell RR's nodes apart from any other domain's. Adding a
# label is idempotent (Neo4j no-ops re-adding an existing label), so
# this is safe on every re-MERGE, not just first-write.
_UPSERT_PAPER_CYPHER = f"""
MERGE (p:{keys.NEO4J_LABEL_PAPER} {{id: $arxiv_id}})
SET   p:{keys.PROJECT_LABEL}:{keys.SOURCE_LABEL},
      p.title    = coalesce($title,    p.title),
      p.abstract = coalesce($abstract, p.abstract),
      p.published = coalesce(date($published), p.published),
      p.citations             = CASE WHEN $citations             > coalesce(p.citations, 0)             THEN $citations             ELSE coalesce(p.citations, 0)             END,
      p.influential_citations = CASE WHEN $influential_citations > coalesce(p.influential_citations, 0) THEN $influential_citations ELSE coalesce(p.influential_citations, 0) END,
      p.hn_points       = CASE WHEN $hn_points       > coalesce(p.hn_points, 0)       THEN $hn_points       ELSE coalesce(p.hn_points, 0)       END,
      p.hn_num_comments = CASE WHEN $hn_num_comments > coalesce(p.hn_num_comments, 0) THEN $hn_num_comments ELSE coalesce(p.hn_num_comments, 0) END,
      p.hf_upvotes      = CASE WHEN $hf_upvotes      > coalesce(p.hf_upvotes, 0)      THEN $hf_upvotes      ELSE coalesce(p.hf_upvotes, 0)      END,
      p.signal = coalesce($signal, p.signal),
      p.updated_at = datetime()
WITH p
UNWIND $sources AS source_name
    MERGE (s:{keys.NEO4J_LABEL_SOURCE} {{name: source_name}})
    SET   s:{keys.PROJECT_LABEL}:{keys.SOURCE_LABEL}
    MERGE (p)-[:{keys.NEO4J_REL_FROM}]->(s)
WITH p
UNWIND $authors AS author_name
    MERGE (a:{keys.NEO4J_LABEL_AUTHOR} {{name: author_name}})
    SET   a:{keys.PROJECT_LABEL}:{keys.SOURCE_LABEL}
    MERGE (a)-[:{keys.NEO4J_REL_AUTHORED}]->(p)
WITH p
UNWIND $categories AS concept_name
    MERGE (c:{keys.NEO4J_LABEL_CONCEPT} {{name: concept_name}})
    SET   c:{keys.PROJECT_LABEL}:{keys.SOURCE_LABEL}
    MERGE (p)-[:{keys.NEO4J_REL_ABOUT}]->(c)
RETURN DISTINCT p.id AS paper_id
"""


async def upsert_paper(
    paper: entities.NormalizedPaper, *, signal: float | None = None,
) -> str:
    """Upsert a NormalizedPaper into the graph. Returns the merged paper's id.

    Pre-conditions: paper.arxiv_id must be non-None (cross-source dedup is
    keyed by it). Callers that pass papers without an arxiv_id should
    either skip them or assign a placeholder id before calling here.

    Side-effects: creates/updates :Paper node + :Source / :Author / :Concept
    nodes + the corresponding [:FROM] / [:AUTHORED] / [:ABOUT] relationships.
    """
    if not paper.arxiv_id:
        raise ValueError("[rr-neo4j] upsert_paper requires paper.arxiv_id != None")
    cypher_params: dict[str, Any] = {
        "arxiv_id":              paper.arxiv_id,
        "title":                 paper.title    or None,
        "abstract":              paper.abstract or None,
        "published":             paper.published.isoformat() if paper.published else None,
        "citations":             int(paper.citations),
        "influential_citations": int(paper.influential_citations),
        "hn_points":             int(paper.hn_points),
        "hn_num_comments":       int(paper.hn_num_comments),
        "hf_upvotes":            int(paper.hf_upvotes),
        "signal":                float(signal) if signal is not None else None,
        "sources":               sorted(paper.sources),
        "authors":               [a for a in paper.authors if a],
        "categories":            [c for c in paper.categories if c],
    }
    driver: AsyncDriver = infra.neo4j.service.get_driver()
    async with driver.session(database=infra.neo4j.params.NEO4J_DATABASE) as session:
        result = await session.run(_UPSERT_PAPER_CYPHER, cypher_params)
        record = await result.single()
    return record["paper_id"] if record else paper.arxiv_id


# Read paths — used by synthesis (concept clusters) and report (top-N).
# Kept minimal in step 3; expand as the synthesis subagent's needs solidify.
async def get_paper_count() -> int:
    """Total :Paper nodes. Cheap sanity check for the bootstrap smoke test."""
    driver: AsyncDriver = infra.neo4j.service.get_driver()
    async with driver.session(database=infra.neo4j.params.NEO4J_DATABASE) as session:
        result = await session.run(f"MATCH (p:{keys.NEO4J_LABEL_PAPER}) RETURN count(p) AS n")
        record = await result.single()
    return int(record["n"]) if record else 0


# ---------------------------------------------------------------------------
# Postgres — radar_scans · findings · seen · profiles
# ---------------------------------------------------------------------------

# Bootstrap — CREATE TABLE IF NOT EXISTS for all 4 RR tables. Idempotent.
_PG_DDL = f"""
CREATE TABLE IF NOT EXISTS {keys.PG_TABLE_SCANS} (
    id                  UUID         PRIMARY KEY,
    profile_id          TEXT         NOT NULL,
    status              TEXT         NOT NULL,
    started_at          TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    finished_at         TIMESTAMPTZ,
    total_candidates    INT          NOT NULL DEFAULT 0,
    total_in_digest     INT          NOT NULL DEFAULT 0,
    error               TEXT,
    -- Per-scan request shape (2026-06-15) — what the operator asked for.
    -- Surfaced in the Recent-scans dropdown so the operator can tell two
    -- scans apart at a glance ("deep agents" vs "constrained decoding").
    topic               TEXT,
    verticals           TEXT[],
    top_n               INT
);
-- Idempotent ADDs for already-deployed environments.
ALTER TABLE {keys.PG_TABLE_SCANS} ADD COLUMN IF NOT EXISTS topic        TEXT;
ALTER TABLE {keys.PG_TABLE_SCANS} ADD COLUMN IF NOT EXISTS verticals    TEXT[];
ALTER TABLE {keys.PG_TABLE_SCANS} ADD COLUMN IF NOT EXISTS top_n        INT;
-- 2026-06-17: per-scan LLM telemetry snapshot. Redis is the in-flight
-- cache (TTL-bound); this column is the durable archive written at
-- scan completion. Read path: Redis-first, falls back to this JSONB
-- when Redis returns empty. NULL on old rows + scans with zero LLM
-- activity (snapshot is skipped to keep the column sparse).
ALTER TABLE {keys.PG_TABLE_SCANS} ADD COLUMN IF NOT EXISTS llm_counters JSONB;
-- 2026-06-17: scan-wide synthesis output — cross-paper themes (3-7
-- names spanning ≥2 papers each) + executive summary (2-3 sentences).
-- Written by `../service.py::persist_scan_result` at scan completion;
-- surfaced in ScanResult so the Digest page can render the themes
-- filter strip + summary without a separate MinIO fetch.
ALTER TABLE {keys.PG_TABLE_SCANS} ADD COLUMN IF NOT EXISTS synthesis_themes  JSONB;
ALTER TABLE {keys.PG_TABLE_SCANS} ADD COLUMN IF NOT EXISTS synthesis_summary TEXT;

CREATE TABLE IF NOT EXISTS {keys.PG_TABLE_FINDINGS} (
    scan_id     UUID  NOT NULL REFERENCES {keys.PG_TABLE_SCANS}(id) ON DELETE CASCADE,
    arxiv_id    TEXT  NOT NULL,
    rank        INT   NOT NULL,
    signal      DOUBLE PRECISION NOT NULL,
    digest_json JSONB NOT NULL,
    PRIMARY KEY (scan_id, arxiv_id)
);

CREATE TABLE IF NOT EXISTS {keys.PG_TABLE_SEEN} (
    profile_id  TEXT         NOT NULL,
    arxiv_id    TEXT         NOT NULL,
    first_seen  TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    PRIMARY KEY (profile_id, arxiv_id)
);

CREATE TABLE IF NOT EXISTS {keys.PG_TABLE_PROFILES} (
    id          TEXT         PRIMARY KEY,
    interests   JSONB        NOT NULL,
    weights     JSONB        NOT NULL,
    created_at  TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at  TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_radar_scans_profile_status
    ON {keys.PG_TABLE_SCANS} (profile_id, status);
CREATE INDEX IF NOT EXISTS idx_radar_findings_signal
    ON {keys.PG_TABLE_FINDINGS} (scan_id, signal DESC);
""".strip()


async def bootstrap_postgres() -> None:
    """Create RR's tables + indexes if missing. Idempotent. Call once at
    FastAPI lifespan startup (or before the first scan)."""
    async with await psycopg.AsyncConnection.connect(
        domains.dd.planner.keys.postgres_url(),
    ) as conn:
        async with conn.cursor() as cur:
            await cur.execute(_PG_DDL)
        await conn.commit()
    logger.info("[rr-pg] bootstrap complete (4 tables + 2 indexes ensured)")


# Scan lifecycle
async def create_scan(
    scan_id:    UUID,
    profile_id: str,
    *,
    topic:      str | None       = None,
    verticals:  list[str] | None = None,
    top_n:      int | None       = None,
) -> None:
    """INSERT a fresh scan row in `pending` status. The Celery task moves
    it to `running` when work starts and `done`/`error` at the end. The
    request shape (topic + verticals + top_n) is persisted alongside so
    the Recent-scans dropdown can show what each scan was searching for."""
    async with await psycopg.AsyncConnection.connect(
        domains.dd.planner.keys.postgres_url(),
    ) as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                f"INSERT INTO {keys.PG_TABLE_SCANS} "
                f"(id, profile_id, status, topic, verticals, top_n) "
                f"VALUES (%s, %s, %s, %s, %s, %s)",
                (
                    str(scan_id), profile_id, keys.SCAN_STATUS_PENDING,
                    topic, list(verticals or []) or None, top_n,
                ),
            )
        await conn.commit()


async def mark_scan_running(scan_id: UUID) -> None:
    """Flip pending → running. No-op if already past pending."""
    async with await psycopg.AsyncConnection.connect(
        domains.dd.planner.keys.postgres_url(),
    ) as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                f"UPDATE {keys.PG_TABLE_SCANS} SET status = %s "
                f"WHERE id = %s AND status = %s",
                (keys.SCAN_STATUS_RUNNING, str(scan_id), keys.SCAN_STATUS_PENDING),
            )
        await conn.commit()


async def mark_scan_done(
    scan_id: UUID,
    *,
    total_candidates: int,
    total_in_digest: int,
) -> None:
    """Mark the scan complete + write the counts. Sets finished_at = NOW()."""
    async with await psycopg.AsyncConnection.connect(
        domains.dd.planner.keys.postgres_url(),
    ) as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                f"UPDATE {keys.PG_TABLE_SCANS} SET "
                f"  status = %s, "
                f"  finished_at = NOW(), "
                f"  total_candidates = %s, "
                f"  total_in_digest = %s "
                f"WHERE id = %s",
                (keys.SCAN_STATUS_DONE, total_candidates, total_in_digest, str(scan_id)),
            )
        await conn.commit()


async def mark_scan_error(scan_id: UUID, *, status: str, error: str) -> None:
    """Mark the scan failed/cancelled with a short error string. Caller
    passes the terminal status (SCAN_STATUS_ERROR or SCAN_STATUS_CANCELLED)."""
    async with await psycopg.AsyncConnection.connect(
        domains.dd.planner.keys.postgres_url(),
    ) as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                f"UPDATE {keys.PG_TABLE_SCANS} SET "
                f"  status = %s, finished_at = NOW(), error = %s "
                f"WHERE id = %s",
                (status, error[:1000], str(scan_id)),
            )
        await conn.commit()


# Findings — one row per digest item; idempotent on (scan_id, arxiv_id)
async def record_findings(scan_id: UUID, findings: list[entities.Finding]) -> int:
    """Bulk-insert findings for a scan. Returns the row count written.
    Conflicts on (scan_id, arxiv_id) are skipped — re-runs are safe."""
    if not findings:
        return 0
    rows = [
        (
            str(scan_id),
            f.arxiv_id,
            f.rank,
            f.signal,
            Jsonb(_finding_as_dict(f)),
        )
        for f in findings
    ]
    async with await psycopg.AsyncConnection.connect(
        domains.dd.planner.keys.postgres_url(),
    ) as conn:
        async with conn.cursor() as cur:
            await cur.executemany(
                f"INSERT INTO {keys.PG_TABLE_FINDINGS} "
                f"(scan_id, arxiv_id, rank, signal, digest_json) "
                f"VALUES (%s, %s, %s, %s, %s) "
                f"ON CONFLICT (scan_id, arxiv_id) DO NOTHING",
                rows,
            )
        await conn.commit()
    return len(rows)


async def get_finding_digest_json(scan_id: UUID, arxiv_id: str) -> dict[str, Any] | None:
    """Read one finding's denormalized `digest_json` row — the shape
    `_finding_as_dict` writes. Used by the code-synth Celery task to
    recover a finding's extraction fields without a request-scoped
    payload (the task runs in the worker process, not the API request)."""
    async with await psycopg.AsyncConnection.connect(
        domains.dd.planner.keys.postgres_url(),
    ) as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                f"SELECT digest_json FROM {keys.PG_TABLE_FINDINGS} "
                f"WHERE scan_id = %s AND arxiv_id = %s",
                (str(scan_id), arxiv_id),
            )
            row = await cur.fetchone()
    if row is None:
        return None
    return row[0] or {}


def _finding_as_dict(f: entities.Finding) -> dict[str, Any]:
    """JSONB payload — denormalized snapshot for the digest renderer
    (FastHTML reads radar_findings.digest_json directly without joining
    other tables)."""
    return {
        "arxiv_id":   f.arxiv_id,
        "rank":       f.rank,
        "signal":     f.signal,
        "title":      f.title,
        "authors":    list(f.authors),
        "summary":    f.summary,
        "is_new":     f.is_new,
        "themes":     list(f.themes),
        "sources":    sorted(f.sources),
        "extraction": _extraction_as_dict(f.extraction) if f.extraction else None,
    }


def _extraction_as_dict(e: Any) -> dict[str, Any]:
    return {
        "arxiv_id":     e.arxiv_id,
        "problem":      e.problem,
        "method":       e.method,
        "math":         e.math,
        "how_to_build": e.how_to_build,
        "money_angle":  e.money_angle,
        "confidence":   e.confidence,
    }


# Seen-set — what arxiv_ids has the profile already encountered? Drives
# the digest's "New since last scan" section via domain.diff_vs_seen.
async def mark_seen_batch(profile_id: str, arxiv_ids: list[str]) -> int:
    """Insert the given (profile_id, arxiv_id) pairs into radar_seen.
    Conflicts are silently dropped (already-seen)."""
    if not arxiv_ids:
        return 0
    rows = [(profile_id, aid) for aid in arxiv_ids if aid]
    if not rows:
        return 0
    async with await psycopg.AsyncConnection.connect(
        domains.dd.planner.keys.postgres_url(),
    ) as conn:
        async with conn.cursor() as cur:
            await cur.executemany(
                f"INSERT INTO {keys.PG_TABLE_SEEN} (profile_id, arxiv_id) "
                f"VALUES (%s, %s) "
                f"ON CONFLICT (profile_id, arxiv_id) DO NOTHING",
                rows,
            )
        await conn.commit()
    return len(rows)


async def get_seen_ids(profile_id: str) -> frozenset[str]:
    """All arxiv_ids ever surfaced to the profile. Fed into diff_vs_seen."""
    async with await psycopg.AsyncConnection.connect(
        domains.dd.planner.keys.postgres_url(),
    ) as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                f"SELECT arxiv_id FROM {keys.PG_TABLE_SEEN} WHERE profile_id = %s",
                (profile_id,),
            )
            rows = await cur.fetchall()
    return frozenset(r[0] for r in rows)


async def write_synthesis_meta(
    scan_id: UUID,
    *,
    themes: list[str],
    summary: str | None,
) -> bool:
    """Write the scan-wide synthesis output to the radar_scans row.
    Themes is the cross-paper theme list (3-7 names); summary is the
    executive paragraph. Both can be empty/None (degraded scans).
    Returns True if a row was updated."""
    async with await psycopg.AsyncConnection.connect(
        domains.dd.planner.keys.postgres_url(),
    ) as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                f"UPDATE {keys.PG_TABLE_SCANS} "
                f"SET synthesis_themes = %s::jsonb, synthesis_summary = %s "
                f"WHERE id = %s",
                (
                    json.dumps(list(themes or []), default=str),
                    summary or None,
                    str(scan_id),
                ),
            )
            n = cur.rowcount
        await conn.commit()
    return bool(n)


async def write_llm_counters(scan_id: UUID, payload: dict) -> bool:
    """UPDATE the scan row with its LLM-counter snapshot. Returns True if
    a row was updated, False if the scan_id didn't match (rare — the
    scan completion path always runs after the row exists).

    Stored as JSONB, so the column can be queried directly:
        SELECT id, llm_counters->'total'->>'calls' AS calls
          FROM radar_scans WHERE finished_at > NOW() - INTERVAL '7 days';

    DELETE on the scan row removes the counters atomically — no separate
    cleanup needed in delete_scan_record."""
    async with await psycopg.AsyncConnection.connect(
        domains.dd.planner.keys.postgres_url(),
    ) as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                f"UPDATE {keys.PG_TABLE_SCANS} SET llm_counters = %s::jsonb "
                f"WHERE id = %s",
                (json.dumps(payload, default=str), str(scan_id)),
            )
            n = cur.rowcount
        await conn.commit()
    return bool(n)


async def read_llm_counters(scan_id: UUID) -> dict | None:
    """Read the persisted LLM-counter snapshot for one scan. Returns the
    parsed dict on hit, None when (a) the scan_id doesn't exist,
    (b) the row exists but llm_counters is NULL (old row OR zero-LLM
    scan whose snapshot was skipped). Called as the Redis-TTL fallback
    from `domains.rr.runtime.llm_counter.service.read_counters`."""
    async with await psycopg.AsyncConnection.connect(
        domains.dd.planner.keys.postgres_url(),
    ) as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                f"SELECT llm_counters FROM {keys.PG_TABLE_SCANS} WHERE id = %s",
                (str(scan_id),),
            )
            row = await cur.fetchone()
    if row is None:
        return None
    payload = row[0]
    # psycopg3 returns JSONB as dict; defensive parse for str variants.
    if isinstance(payload, str):
        try:
            return json.loads(payload)
        except Exception:
            return None
    return payload if isinstance(payload, dict) else None


async def delete_scan_record(scan_id: UUID) -> bool:
    """Delete one scan + its findings (CASCADE) from Postgres. Returns
    True if a row existed, False if the scan_id wasn't found. radar_seen
    entries are NOT touched — the operator's "I've seen this paper before"
    memory is profile-scoped, not scan-scoped. Neo4j and Qdrant are also
    left untouched (accumulated cross-scan knowledge).

    Per-scan LLM-counter snapshot (llm_counters JSONB column on the same
    row) is removed atomically with the row — no separate cleanup."""
    async with await psycopg.AsyncConnection.connect(
        domains.dd.planner.keys.postgres_url(),
    ) as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                f"DELETE FROM {keys.PG_TABLE_SCANS} WHERE id = %s",
                (str(scan_id),),
            )
            n = cur.rowcount
        await conn.commit()
    return bool(n)


async def reset_seen(profile_id: str) -> int:
    """Truncate the profile's `radar_seen` rows so every paper in the next
    scan reads as `is_new = True` again. Returns the row count that was
    deleted. Operator-triggered (POST /profile/{id}/reset-seen) — never
    called from the scan pipeline."""
    async with await psycopg.AsyncConnection.connect(
        domains.dd.planner.keys.postgres_url(),
    ) as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                f"DELETE FROM {keys.PG_TABLE_SEEN} WHERE profile_id = %s",
                (profile_id,),
            )
            n = cur.rowcount
        await conn.commit()
    return int(n or 0)


# Profiles — interest verticals + per-profile SignalWeights overrides
async def get_profile(profile_id: str) -> dict[str, Any] | None:
    """Fetch a profile's interests + weights. Returns None if missing."""
    async with await psycopg.AsyncConnection.connect(
        domains.dd.planner.keys.postgres_url(),
    ) as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                f"SELECT id, interests, weights, created_at, updated_at "
                f"FROM {keys.PG_TABLE_PROFILES} WHERE id = %s",
                (profile_id,),
            )
            row = await cur.fetchone()
    if row is None:
        return None
    return {
        "id":         row[0],
        "interests":  row[1],   # psycopg auto-decodes JSONB → dict
        "weights":    row[2],
        "created_at": row[3],
        "updated_at": row[4],
    }


async def upsert_profile(
    profile_id: str,
    *,
    interests: dict[str, Any],
    weights: dict[str, Any],
) -> None:
    """INSERT a profile or UPDATE its interests/weights in place. updated_at
    is bumped on every call."""
    async with await psycopg.AsyncConnection.connect(
        domains.dd.planner.keys.postgres_url(),
    ) as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                f"INSERT INTO {keys.PG_TABLE_PROFILES} "
                f"(id, interests, weights) VALUES (%s, %s, %s) "
                f"ON CONFLICT (id) DO UPDATE SET "
                f"  interests = EXCLUDED.interests, "
                f"  weights = EXCLUDED.weights, "
                f"  updated_at = NOW()",
                (profile_id, Jsonb(interests), Jsonb(weights)),
            )
        await conn.commit()


# ---------------------------------------------------------------------------
# Qdrant — `radar_papers` collection (paper-abstract vectors)
# ---------------------------------------------------------------------------

async def bootstrap_qdrant() -> None:
    """Ensure `radar_papers` collection exists with the right vector config
    + payload indexes. Safe to re-run."""
    client = infra.qdrant.service.get_qdrant()
    exists = await client.collection_exists(collection_name=keys.QDRANT_COLLECTION)
    if exists:
        logger.info(f"[rr-qdrant] collection {keys.QDRANT_COLLECTION!r} already exists")
    else:
        await client.create_collection(
            collection_name = keys.QDRANT_COLLECTION,
            vectors_config  = VectorParams(
                size     = params.STORES_PARAMS.qdrant_vector_dim,
                distance = Distance.COSINE,
            ),
            optimizers_config = OptimizersConfigDiff(
                default_segment_number = params.STORES_PARAMS.qdrant_segment_count,
            ),
        )
        logger.info(
            f"[rr-qdrant] created collection {keys.QDRANT_COLLECTION!r} "
            f"(dim={params.STORES_PARAMS.qdrant_vector_dim}, distance=COSINE)"
        )
    # Payload indexes — idempotent (Qdrant ignores duplicates). These speed
    # up filter+search by 10-100× on the radar's typical queries.
    for field, schema in (
        (keys.QDRANT_PAYLOAD_ARXIV_ID,  PayloadSchemaType.KEYWORD),
        (keys.QDRANT_PAYLOAD_SIGNAL,    PayloadSchemaType.FLOAT),
        (keys.QDRANT_PAYLOAD_PUBLISHED, PayloadSchemaType.DATETIME),
        (keys.QDRANT_PAYLOAD_SOURCES,   PayloadSchemaType.KEYWORD),
    ):
        try:
            await client.create_payload_index(
                collection_name = keys.QDRANT_COLLECTION,
                field_name      = field,
                field_schema    = schema,
            )
        except Exception as e:
            # Idempotency: an already-existing index raises in some
            # qdrant-client versions; log + continue.
            logger.debug(f"[rr-qdrant] payload index {field!r} exists or skip: {e}")


# Point IDs — deterministic UUIDs from arxiv_id so re-upserts overwrite in
# place (Qdrant requires integer or UUID point ids; arxiv_id is a string).
_QDRANT_POINT_NAMESPACE = uuid5(NAMESPACE_URL, "rr.point.arxiv")


def _point_id(arxiv_id: str) -> str:
    """Deterministic UUIDv5 for the arxiv_id → repeat upserts are
    idempotent at the Qdrant layer."""
    return str(uuid5(_QDRANT_POINT_NAMESPACE, arxiv_id))


async def upsert_paper_vector(
    paper: entities.NormalizedPaper,
    *,
    embedding: list[float] | tuple[float, ...],
    signal: float | None = None,
) -> str:
    """Upsert a paper's vector + payload. Returns the point id.

    Pre-conditions: paper.arxiv_id must be non-None; embedding length must
    match QDRANT_VECTOR_DIM (validated by the qdrant client itself — we
    don't pre-check here)."""
    if not paper.arxiv_id:
        raise ValueError("[rr-qdrant] upsert_paper_vector requires arxiv_id != None")
    point = PointStruct(
        id      = _point_id(paper.arxiv_id),
        vector  = list(embedding),
        payload = {
            keys.QDRANT_PAYLOAD_ARXIV_ID:  paper.arxiv_id,
            keys.QDRANT_PAYLOAD_SIGNAL:    float(signal) if signal is not None else 0.0,
            keys.QDRANT_PAYLOAD_PUBLISHED: paper.published.isoformat() if paper.published else None,
            keys.QDRANT_PAYLOAD_SOURCES:   sorted(paper.sources),
            "title":                       paper.title,
            "authors":                     list(paper.authors),
            "categories":                  list(paper.categories),
            "citations":                   int(paper.citations),
            "hn_points":                   int(paper.hn_points),
            "hf_upvotes":                  int(paper.hf_upvotes),
        },
    )
    client = infra.qdrant.service.get_qdrant()
    await client.upsert(collection_name=keys.QDRANT_COLLECTION, points=[point])
    return point.id


async def search_by_embedding(
    query_vector: list[float] | tuple[float, ...],
    *,
    limit: int = 20,
    arxiv_ids: tuple[str, ...] | None = None,
) -> list[dict[str, Any]]:
    """k-NN search over `radar_papers`. When `arxiv_ids` is set, restricts
    to that subset (used by synthesis's "cluster these specific papers").
    Returns dicts {arxiv_id, score, payload}.

    2026-09-19: `AsyncQdrantClient.search()` was removed in qdrant-client
    1.16 (same live-confirmed break YCS's retriever/query paths hit — see
    `domains/ycs/retriever/service.py`); `query_points()` is the
    replacement, returning `.points` instead of a bare list."""
    flt: Filter | None = None
    if arxiv_ids:
        flt = Filter(
            must=[
                FieldCondition(
                    key   = keys.QDRANT_PAYLOAD_ARXIV_ID,
                    match = MatchValue(value=aid),
                )
                for aid in arxiv_ids
            ]
        )
    client = infra.qdrant.service.get_qdrant()
    response = await client.query_points(
        collection_name = keys.QDRANT_COLLECTION,
        query           = list(query_vector),
        query_filter    = flt,
        limit           = limit,
        with_payload    = True,
    )
    return [
        {
            "arxiv_id": r.payload.get(keys.QDRANT_PAYLOAD_ARXIV_ID) if r.payload else None,
            "score":    r.score,
            "payload":  r.payload or {},
        }
        for r in response.points
    ]


async def count_points() -> int:
    """Total points in `radar_papers`. Cheap sanity check for bootstrap."""
    client = infra.qdrant.service.get_qdrant()
    info = await client.get_collection(collection_name=keys.QDRANT_COLLECTION)
    return int(getattr(info, "points_count", 0) or 0)
