"""RR resource boundary — thin `@mcp.resource` shells.

Per docs/CODE-CONVENTIONS.md §4: URI binding + response shaping only.
Postgres / MinIO / Neo4j I/O lives in `service.py`, envelopes +
validation in `domain.py`, URIs in `keys.py`, tunables in `params.py`.
Grows by appending one wrapper per resource; split only if this file
passes ~100 lines.
"""
from __future__ import annotations
from . import domain, keys, service

import json
import logging

from fastmcp import FastMCP


logger = logging.getLogger(__name__)


def register(mcp: FastMCP) -> None:
    """Register all RR resources on the root server."""

    @mcp.resource(keys.LATEST_DIGEST_URI)
    async def latest_digest() -> str:
        """Return the most-recent completed scan's digest as a JSON string.

        Use this to bootstrap an agent's context without re-running a scan
        — e.g. a follow-up "explain paper #3 in more depth" workflow.
        """
        try:
            scan_id = await service.fetch_latest_done_scan_id()
        except Exception as e:
            logger.warning(f"[rr-resource:latest_digest] scan lookup failed: {e}")
            return json.dumps({
                "error": f"{type(e).__name__}: {str(e)[:200]}",
            })
        if not scan_id:
            return json.dumps(domain.no_scan_payload())
        try:
            digest = await service.load_digest_from_minio(scan_id)
        except Exception as e:
            logger.warning(f"[rr-resource:latest_digest] minio load failed: {e}")
            return json.dumps({
                "error": f"{type(e).__name__}: {str(e)[:200]}",
                "scan_id": scan_id,
            })
        if digest is None:
            return json.dumps(domain.missing_digest_payload(scan_id))
        return json.dumps(digest, default=str)

    @mcp.resource(keys.CONCEPT_URI_TEMPLATE)
    async def concept(name: str) -> str:
        """Return a JSON blob with this concept's papers + related concepts.

        Args:
            name: The concept name as stored in Neo4j (e.g. 'constrained_decoding').
                  Case-sensitive — Neo4j MERGE was case-preserving.
        """
        err = domain.validate_concept_name(name)
        if err:
            return json.dumps({"error": err})
        try:
            payload = await service.fetch_concept_payload(name)
        except Exception as e:
            logger.warning(f"[rr-resource:concept] {name!r} query failed: {e}")
            return json.dumps({"error": f"{type(e).__name__}: {str(e)[:200]}"})
        if payload is None:
            return json.dumps(domain.concept_not_found_payload(name))
        return json.dumps(payload, default=str)
