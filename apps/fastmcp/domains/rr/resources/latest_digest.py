"""Resource: `radar://latest_digest`

Returns the most recent COMPLETED scan's digest as JSON. The MCP client
reads this via `read_resource("radar://latest_digest")` — no tool call.

Implementation: queries Postgres for the latest `radar_scans` row with
`status='done'`, then loads the digest from MinIO via the canonical
`rr/scans/{scan_id}/digest.json` path. This decouples the MCP client
from the FastAPI app — the resource is a direct read of the source of
truth.
"""
from __future__ import annotations

import json
import logging
import os
from typing import Any

import psycopg
from fastmcp import FastMCP

# We can't import the FastAPI app's storage code from FastMCP (separate
# Python image, different dep set). Reuse the env vars + DSN logic via
# small local helpers.
import aioboto3
from botocore.exceptions import ClientError


logger = logging.getLogger(__name__)


def _postgres_url() -> str:
    """Build the Postgres URL from env. Mirrors the fastapi side."""
    from urllib.parse import quote
    user     = quote(os.environ["POSTGRES_USER"], safe="")
    password = quote(os.environ["POSTGRES_PASSWORD"], safe="")
    host     = os.environ["POSTGRES_HOST"]
    port     = os.environ["POSTGRES_PORT"]
    db       = os.environ["POSTGRES_DATABASE"]
    auth = f"{user}:{password}@" if password else f"{user}@"
    return f"postgresql://{auth}{host}:{port}/{db}"


async def _fetch_latest_done_scan_id() -> str | None:
    """Most-recent radar_scans row with status='done'. None if no done scan."""
    async with await psycopg.AsyncConnection.connect(_postgres_url()) as c:
        async with c.cursor() as cur:
            await cur.execute(
                "SELECT id FROM radar_scans "
                "WHERE status = 'done' "
                "ORDER BY finished_at DESC NULLS LAST "
                "LIMIT 1"
            )
            row = await cur.fetchone()
    return str(row[0]) if row else None


async def _load_digest_from_minio(scan_id: str) -> dict[str, Any] | None:
    """GET rr/scans/{scan_id}/digest.json from MinIO."""
    bucket   = os.environ["MINIO_BUCKET_COELHONEXUS"]
    endpoint = os.environ["MINIO_ENDPOINT"].strip()
    key      = f"rr/scans/{scan_id}/digest.json"
    session  = aioboto3.Session()
    async with session.client(
        "s3",
        endpoint_url          = endpoint,
        aws_access_key_id     = os.environ["AWS_ACCESS_KEY_ID"],
        aws_secret_access_key = os.environ["AWS_SECRET_ACCESS_KEY"],
        region_name           = "us-east-1",
    ) as s3:
        try:
            obj = await s3.get_object(Bucket=bucket, Key=key)
        except ClientError as e:
            code = (e.response or {}).get("Error", {}).get("Code", "")
            if code in ("404", "NoSuchKey"):
                return None
            raise
        body = await obj["Body"].read()
    return json.loads(body)


def register(mcp: FastMCP) -> None:
    """Register `radar://latest_digest` on the root server."""

    @mcp.resource("radar://latest_digest")
    async def latest_digest() -> str:
        """Return the most-recent completed scan's digest as a JSON string.

        Use this to bootstrap an agent's context without re-running a scan
        — e.g. a follow-up "explain paper #3 in more depth" workflow.
        """
        scan_id = await _fetch_latest_done_scan_id()
        if not scan_id:
            return json.dumps({
                "error": "No completed scan found",
                "hint":  "Run POST /api/v1/rr/scan from FastHTML first.",
            })
        digest = await _load_digest_from_minio(scan_id)
        if digest is None:
            return json.dumps({
                "error":   "digest.json missing from MinIO",
                "scan_id": scan_id,
                "hint":    "Postgres says done but MinIO doesn't have the artifact",
            })
        return json.dumps(digest, default=str)
