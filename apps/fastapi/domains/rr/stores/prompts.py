"""stores prompts — datastore query text (Cypher + Postgres DDL)."""
from __future__ import annotations

from .. import keys


# onto the same node so cross-source ingest collapses correctly.
#
# 2026-09-17: every node gets `:COELHONexus:RR` stamped on via `SET
# n:{PROJECT_LABEL}:{SOURCE_LABEL}` — mirrors YCS's `:COELHONexus:YCS`
# tagging (`domains/ycs/graph_builder/service.py`) so the shared Neo4j
# instance can tell RR's nodes apart from any other domain's. Adding a
# label is idempotent (Neo4j no-ops re-adding an existing label), so
# this is safe on every re-MERGE, not just first-write.
UPSERT_PAPER_CYPHER = f"""
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


PG_DDL = f"""
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
