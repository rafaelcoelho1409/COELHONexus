"""Postgres I/O for RR — radar_scans · findings · seen · profiles.

Per docs/CODE-CONVENTIONS.md §service: async + I/O lives here, no
business logic. The 4 tables are created idempotently via
`bootstrap_postgres()` at FastAPI lifespan startup (architecture doc
§2.4.3).

Connection model: one `psycopg.AsyncConnection.connect()` per logical
operation. The radar is not high-throughput (a few scans/day per user)
so a per-op connection has acceptable overhead and keeps the code
free of pool-lifecycle concerns. Promote to AsyncConnectionPool when
multi-tenant SaaS lands (v2).
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

import psycopg
from psycopg.types.json import Jsonb

# Pragmatic cross-domain import: postgres_url() lives in the planner
# package today; TODO promote to apps/fastapi/infra/postgres/ alongside
# the planner checkpointer (same coupling already exists for the agent).
from ...dd.planner.keys import postgres_url
from ..entities import Finding
from ..keys import (
    PG_TABLE_FINDINGS,
    PG_TABLE_PROFILES,
    PG_TABLE_SCANS,
    PG_TABLE_SEEN,
    SCAN_STATUS_DONE,
    SCAN_STATUS_PENDING,
    SCAN_STATUS_RUNNING,
)
from ..params import STORES_PARAMS


logger = logging.getLogger(__name__)


# Bootstrap — CREATE TABLE IF NOT EXISTS for all 4 RR tables. Idempotent.
_DDL = f"""
CREATE TABLE IF NOT EXISTS {PG_TABLE_SCANS} (
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
ALTER TABLE {PG_TABLE_SCANS} ADD COLUMN IF NOT EXISTS topic        TEXT;
ALTER TABLE {PG_TABLE_SCANS} ADD COLUMN IF NOT EXISTS verticals    TEXT[];
ALTER TABLE {PG_TABLE_SCANS} ADD COLUMN IF NOT EXISTS top_n        INT;
-- 2026-06-17: per-scan LLM telemetry snapshot. Redis is the in-flight
-- cache (TTL-bound); this column is the durable archive written at
-- scan completion. Read path: Redis-first, falls back to this JSONB
-- when Redis returns empty. NULL on old rows + scans with zero LLM
-- activity (snapshot is skipped to keep the column sparse).
ALTER TABLE {PG_TABLE_SCANS} ADD COLUMN IF NOT EXISTS llm_counters JSONB;
-- 2026-06-17: scan-wide synthesis output — cross-paper themes (3-7
-- names spanning ≥2 papers each) + executive summary (2-3 sentences).
-- Written by `persist_scan_result` at scan completion; surfaced in
-- ScanResult so the Digest page can render the themes filter strip
-- + summary without a separate MinIO fetch.
ALTER TABLE {PG_TABLE_SCANS} ADD COLUMN IF NOT EXISTS synthesis_themes  JSONB;
ALTER TABLE {PG_TABLE_SCANS} ADD COLUMN IF NOT EXISTS synthesis_summary TEXT;

CREATE TABLE IF NOT EXISTS {PG_TABLE_FINDINGS} (
    scan_id     UUID  NOT NULL REFERENCES {PG_TABLE_SCANS}(id) ON DELETE CASCADE,
    arxiv_id    TEXT  NOT NULL,
    rank        INT   NOT NULL,
    signal      DOUBLE PRECISION NOT NULL,
    digest_json JSONB NOT NULL,
    PRIMARY KEY (scan_id, arxiv_id)
);

CREATE TABLE IF NOT EXISTS {PG_TABLE_SEEN} (
    profile_id  TEXT         NOT NULL,
    arxiv_id    TEXT         NOT NULL,
    first_seen  TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    PRIMARY KEY (profile_id, arxiv_id)
);

CREATE TABLE IF NOT EXISTS {PG_TABLE_PROFILES} (
    id          TEXT         PRIMARY KEY,
    interests   JSONB        NOT NULL,
    weights     JSONB        NOT NULL,
    created_at  TIMESTAMPTZ  NOT NULL DEFAULT NOW(),
    updated_at  TIMESTAMPTZ  NOT NULL DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_radar_scans_profile_status
    ON {PG_TABLE_SCANS} (profile_id, status);
CREATE INDEX IF NOT EXISTS idx_radar_findings_signal
    ON {PG_TABLE_FINDINGS} (scan_id, signal DESC);
""".strip()


async def bootstrap_postgres() -> None:
    """Create RR's tables + indexes if missing. Idempotent. Call once at
    FastAPI lifespan startup (or before the first scan)."""
    async with await psycopg.AsyncConnection.connect(postgres_url()) as conn:
        async with conn.cursor() as cur:
            await cur.execute(_DDL)
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
    async with await psycopg.AsyncConnection.connect(postgres_url()) as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                f"INSERT INTO {PG_TABLE_SCANS} "
                f"(id, profile_id, status, topic, verticals, top_n) "
                f"VALUES (%s, %s, %s, %s, %s, %s)",
                (
                    str(scan_id), profile_id, SCAN_STATUS_PENDING,
                    topic, list(verticals or []) or None, top_n,
                ),
            )
        await conn.commit()


async def mark_scan_running(scan_id: UUID) -> None:
    """Flip pending → running. No-op if already past pending."""
    async with await psycopg.AsyncConnection.connect(postgres_url()) as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                f"UPDATE {PG_TABLE_SCANS} SET status = %s "
                f"WHERE id = %s AND status = %s",
                (SCAN_STATUS_RUNNING, str(scan_id), SCAN_STATUS_PENDING),
            )
        await conn.commit()


async def mark_scan_done(
    scan_id: UUID,
    *,
    total_candidates: int,
    total_in_digest: int,
) -> None:
    """Mark the scan complete + write the counts. Sets finished_at = NOW()."""
    async with await psycopg.AsyncConnection.connect(postgres_url()) as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                f"UPDATE {PG_TABLE_SCANS} SET "
                f"  status = %s, "
                f"  finished_at = NOW(), "
                f"  total_candidates = %s, "
                f"  total_in_digest = %s "
                f"WHERE id = %s",
                (SCAN_STATUS_DONE, total_candidates, total_in_digest, str(scan_id)),
            )
        await conn.commit()


async def mark_scan_error(scan_id: UUID, *, status: str, error: str) -> None:
    """Mark the scan failed/cancelled with a short error string. Caller
    passes the terminal status (SCAN_STATUS_ERROR or SCAN_STATUS_CANCELLED)."""
    async with await psycopg.AsyncConnection.connect(postgres_url()) as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                f"UPDATE {PG_TABLE_SCANS} SET "
                f"  status = %s, finished_at = NOW(), error = %s "
                f"WHERE id = %s",
                (status, error[:1000], str(scan_id)),
            )
        await conn.commit()


# Findings — one row per digest item; idempotent on (scan_id, arxiv_id)
async def record_findings(scan_id: UUID, findings: list[Finding]) -> int:
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
    async with await psycopg.AsyncConnection.connect(postgres_url()) as conn:
        async with conn.cursor() as cur:
            await cur.executemany(
                f"INSERT INTO {PG_TABLE_FINDINGS} "
                f"(scan_id, arxiv_id, rank, signal, digest_json) "
                f"VALUES (%s, %s, %s, %s, %s) "
                f"ON CONFLICT (scan_id, arxiv_id) DO NOTHING",
                rows,
            )
        await conn.commit()
    return len(rows)


def _finding_as_dict(f: Finding) -> dict[str, Any]:
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
    async with await psycopg.AsyncConnection.connect(postgres_url()) as conn:
        async with conn.cursor() as cur:
            await cur.executemany(
                f"INSERT INTO {PG_TABLE_SEEN} (profile_id, arxiv_id) "
                f"VALUES (%s, %s) "
                f"ON CONFLICT (profile_id, arxiv_id) DO NOTHING",
                rows,
            )
        await conn.commit()
    return len(rows)


async def get_seen_ids(profile_id: str) -> frozenset[str]:
    """All arxiv_ids ever surfaced to the profile. Fed into diff_vs_seen."""
    async with await psycopg.AsyncConnection.connect(postgres_url()) as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                f"SELECT arxiv_id FROM {PG_TABLE_SEEN} WHERE profile_id = %s",
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
    import json as _json
    async with await psycopg.AsyncConnection.connect(postgres_url()) as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                f"UPDATE {PG_TABLE_SCANS} "
                f"SET synthesis_themes = %s::jsonb, synthesis_summary = %s "
                f"WHERE id = %s",
                (
                    _json.dumps(list(themes or []), default=str),
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
    import json as _json
    async with await psycopg.AsyncConnection.connect(postgres_url()) as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                f"UPDATE {PG_TABLE_SCANS} SET llm_counters = %s::jsonb "
                f"WHERE id = %s",
                (_json.dumps(payload, default=str), str(scan_id)),
            )
            n = cur.rowcount
        await conn.commit()
    return bool(n)


async def read_llm_counters(scan_id: UUID) -> dict | None:
    """Read the persisted LLM-counter snapshot for one scan. Returns the
    parsed dict on hit, None when (a) the scan_id doesn't exist,
    (b) the row exists but llm_counters is NULL (old row OR zero-LLM
    scan whose snapshot was skipped). Called as the Redis-TTL fallback
    from `runtime/llm_counter.read_counters`."""
    async with await psycopg.AsyncConnection.connect(postgres_url()) as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                f"SELECT llm_counters FROM {PG_TABLE_SCANS} WHERE id = %s",
                (str(scan_id),),
            )
            row = await cur.fetchone()
    if row is None:
        return None
    payload = row[0]
    # psycopg3 returns JSONB as dict; defensive parse for str variants.
    if isinstance(payload, str):
        import json as _json
        try:
            return _json.loads(payload)
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
    async with await psycopg.AsyncConnection.connect(postgres_url()) as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                f"DELETE FROM {PG_TABLE_SCANS} WHERE id = %s",
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
    async with await psycopg.AsyncConnection.connect(postgres_url()) as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                f"DELETE FROM {PG_TABLE_SEEN} WHERE profile_id = %s",
                (profile_id,),
            )
            n = cur.rowcount
        await conn.commit()
    return int(n or 0)


# Profiles — interest verticals + per-profile SignalWeights overrides
async def get_profile(profile_id: str) -> dict[str, Any] | None:
    """Fetch a profile's interests + weights. Returns None if missing."""
    async with await psycopg.AsyncConnection.connect(postgres_url()) as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                f"SELECT id, interests, weights, created_at, updated_at "
                f"FROM {PG_TABLE_PROFILES} WHERE id = %s",
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
    async with await psycopg.AsyncConnection.connect(postgres_url()) as conn:
        async with conn.cursor() as cur:
            await cur.execute(
                f"INSERT INTO {PG_TABLE_PROFILES} "
                f"(id, interests, weights) VALUES (%s, %s, %s) "
                f"ON CONFLICT (id) DO UPDATE SET "
                f"  interests = EXCLUDED.interests, "
                f"  weights = EXCLUDED.weights, "
                f"  updated_at = NOW()",
                (profile_id, Jsonb(interests), Jsonb(weights)),
            )
        await conn.commit()


# Silence unused-import warning for STORES_PARAMS (kept for future use:
# statement-timeout wiring once we promote to a pool).
_ = STORES_PARAMS
