"""Neo4j I/O for RR — Paper · Author · Concept · Source graph.

Per docs/CODE-CONVENTIONS.md §service: async Cypher lives here, no
business logic. The graph schema is created idempotently via
`bootstrap_neo4j()` at FastAPI lifespan startup (architecture doc §2.4.1).

Cross-source dedup payoff: `MERGE (:Paper {id: arxiv_id})` collapses
the same paper found via arxiv + s2 + hf + hn to ONE node with
multiple `[:FROM]` edges. The signal fields (citations, hn_points,
hf_upvotes) are written as MAX of the new value vs whatever's already
on the node — so source-order doesn't matter.

Driver reuse: `infra.neo4j.get_driver()` returns the process-wide
singleton AsyncDriver used by YCS too — no new connection pool.
"""
from __future__ import annotations

import logging
from typing import Any

from neo4j import AsyncDriver

from infra.neo4j import get_driver
from infra.neo4j.params import NEO4J_DATABASE
from ..entities import NormalizedPaper
from ..keys import (
    NEO4J_LABEL_AUTHOR,
    NEO4J_LABEL_CONCEPT,
    NEO4J_LABEL_PAPER,
    NEO4J_LABEL_SOURCE,
    NEO4J_REL_ABOUT,
    NEO4J_REL_AUTHORED,
    NEO4J_REL_FROM,
)


logger = logging.getLogger(__name__)


# Bootstrap — constraints + indexes. Idempotent (IF NOT EXISTS on every
# statement). Run once at startup; safe to re-run.
_BOOTSTRAP_STMTS: tuple[str, ...] = (
    # Uniqueness — guarantees MERGE-by-id is O(1)
    f"CREATE CONSTRAINT paper_id_unique IF NOT EXISTS "
    f"FOR (p:{NEO4J_LABEL_PAPER}) REQUIRE p.id IS UNIQUE",
    f"CREATE CONSTRAINT concept_name_unique IF NOT EXISTS "
    f"FOR (c:{NEO4J_LABEL_CONCEPT}) REQUIRE c.name IS UNIQUE",
    f"CREATE CONSTRAINT source_name_unique IF NOT EXISTS "
    f"FOR (s:{NEO4J_LABEL_SOURCE}) REQUIRE s.name IS UNIQUE",
    # Indexes — payoff for ORDER BY / WHERE clauses used by the synthesis
    # subagent's GraphRAG queries and the digest renderer's top-N pulls.
    f"CREATE INDEX paper_signal_idx IF NOT EXISTS "
    f"FOR (p:{NEO4J_LABEL_PAPER}) ON (p.signal)",
    f"CREATE INDEX paper_published_idx IF NOT EXISTS "
    f"FOR (p:{NEO4J_LABEL_PAPER}) ON (p.published)",
)


async def bootstrap_neo4j() -> None:
    """Create constraints + indexes if missing. Idempotent."""
    driver: AsyncDriver = get_driver()
    async with driver.session(database=NEO4J_DATABASE) as session:
        for stmt in _BOOTSTRAP_STMTS:
            await session.run(stmt)
    logger.info(
        f"[rr-neo4j] bootstrap complete "
        f"({len(_BOOTSTRAP_STMTS)} statements, db={NEO4J_DATABASE!r})"
    )


# Paper upsert — MERGE by arxiv_id; sources / authors / concepts grafted
# onto the same node so cross-source ingest collapses correctly.
_UPSERT_PAPER_CYPHER = f"""
MERGE (p:{NEO4J_LABEL_PAPER} {{id: $arxiv_id}})
SET   p.title    = coalesce($title,    p.title),
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
    MERGE (s:{NEO4J_LABEL_SOURCE} {{name: source_name}})
    MERGE (p)-[:{NEO4J_REL_FROM}]->(s)
WITH p
UNWIND $authors AS author_name
    MERGE (a:{NEO4J_LABEL_AUTHOR} {{name: author_name}})
    MERGE (a)-[:{NEO4J_REL_AUTHORED}]->(p)
WITH p
UNWIND $categories AS concept_name
    MERGE (c:{NEO4J_LABEL_CONCEPT} {{name: concept_name}})
    MERGE (p)-[:{NEO4J_REL_ABOUT}]->(c)
RETURN p.id AS paper_id
"""


async def upsert_paper(paper: NormalizedPaper, *, signal: float | None = None) -> str:
    """Upsert a NormalizedPaper into the graph. Returns the merged paper's id.

    Pre-conditions: paper.arxiv_id must be non-None (cross-source dedup is
    keyed by it). Callers that pass papers without an arxiv_id should
    either skip them or assign a placeholder id before calling here.

    Side-effects: creates/updates :Paper node + :Source / :Author / :Concept
    nodes + the corresponding [:FROM] / [:AUTHORED] / [:ABOUT] relationships.
    """
    if not paper.arxiv_id:
        raise ValueError("[rr-neo4j] upsert_paper requires paper.arxiv_id != None")
    params: dict[str, Any] = {
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
    driver: AsyncDriver = get_driver()
    async with driver.session(database=NEO4J_DATABASE) as session:
        result = await session.run(_UPSERT_PAPER_CYPHER, params)
        record = await result.single()
    return record["paper_id"] if record else paper.arxiv_id


# Read paths — used by synthesis (concept clusters) and report (top-N).
# Kept minimal in step 3; expand as the synthesis subagent's needs solidify.
async def get_paper_count() -> int:
    """Total :Paper nodes. Cheap sanity check for the bootstrap smoke test."""
    driver: AsyncDriver = get_driver()
    async with driver.session(database=NEO4J_DATABASE) as session:
        result = await session.run(f"MATCH (p:{NEO4J_LABEL_PAPER}) RETURN count(p) AS n")
        record = await result.single()
    return int(record["n"]) if record else 0
