"""RR resources — I/O orchestration (Imperative Shell).

Per docs/CODE-CONVENTIONS.md §4: Postgres / MinIO / Neo4j calls live
here; pure shaping delegated to `domain.py`. We can't import the
sibling peer app's storage code (separate Python image, different dep
set), so DSN/client construction is re-implemented locally against the
same env vars.

All helpers are best-effort with bounded waits — a store hiccup
surfaces as None / raises to the caller, never hangs the worker.
"""
from __future__ import annotations
from . import params

import asyncio
import json
import logging
import os
from typing import Any
from urllib.parse import quote

import aioboto3
import psycopg
from botocore.config import Config as BotoConfig
from botocore.exceptions import ClientError
from neo4j import AsyncGraphDatabase


logger = logging.getLogger(__name__)


def postgres_url() -> str:
    """Build the Postgres URL from env. Mirrors the sibling peer app."""
    user = quote(os.environ["POSTGRES_USER"], safe="")
    password = quote(os.environ["POSTGRES_PASSWORD"], safe="")
    host = os.environ["POSTGRES_HOST"]
    port = os.environ["POSTGRES_PORT"]
    db = os.environ["POSTGRES_DATABASE"]
    auth = f"{user}:{password}@" if password else f"{user}@"
    return f"postgresql://{auth}{host}:{port}/{db}"


async def fetch_latest_done_scan_id() -> str | None:
    """Most-recent radar_scans row with status='done'. None if no done scan."""
    async with await psycopg.AsyncConnection.connect(
        postgres_url(), connect_timeout=params.PG_CONNECT_TIMEOUT_S,
    ) as c:
        async with c.cursor() as cur:
            await cur.execute(
                "SELECT id FROM radar_scans "
                "WHERE status = 'done' "
                "ORDER BY finished_at DESC NULLS LAST "
                "LIMIT 1"
            )
            row = await cur.fetchone()
    return str(row[0]) if row else None


async def load_digest_from_minio(scan_id: str) -> dict[str, Any] | None:
    """GET rr/scans/{scan_id}/digest.json from MinIO. None if missing."""
    bucket = os.environ["MINIO_BUCKET_COELHONEXUS"]
    endpoint = os.environ["MINIO_ENDPOINT"].strip()
    key = f"rr/scans/{scan_id}/digest.json"
    session = aioboto3.Session()
    async with session.client(
        "s3",
        endpoint_url=endpoint,
        aws_access_key_id=os.environ["AWS_ACCESS_KEY_ID"],
        aws_secret_access_key=os.environ["AWS_SECRET_ACCESS_KEY"],
        region_name="us-east-1",
        config=BotoConfig(
            signature_version="s3v4",
            connect_timeout=params.MINIO_CONNECT_TIMEOUT_S,
            read_timeout=params.MINIO_READ_TIMEOUT_S,
            retries={"max_attempts": 1, "mode": "standard"},
        ),
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


_CONCEPT_CYPHER = """
MATCH (c:Concept {name: $name})
OPTIONAL MATCH (c)<-[:ABOUT]-(p:Paper)
WITH c, collect(DISTINCT {
    arxiv_id: p.id, title: p.title, signal: p.signal,
    citations: p.citations, published: p.published
}) AS papers
OPTIONAL MATCH (c)<-[:ABOUT]-(:Paper)-[:ABOUT]->(c2:Concept)
WHERE c2.name <> $name
WITH c, papers, collect(DISTINCT c2.name)[..15] AS related_concepts
RETURN {
    name: c.name,
    family: coalesce(c.family, ''),
    papers: papers[..20],
    related_concepts: related_concepts
} AS payload
"""


def neo4j_driver():
    """One-shot Neo4j driver for a resource request. The caller closes it."""
    uri = os.environ["NEO4J_URI"]
    user = os.environ.get("NEO4J_USERNAME", "neo4j")
    pwd = os.environ.get("NEO4J_PASSWORD", "")
    auth = (user, pwd) if pwd else None
    return AsyncGraphDatabase.driver(uri, auth=auth)


async def fetch_concept_payload(name: str) -> dict[str, Any] | None:
    """Neo4j subgraph for one concept. None when the concept is unknown."""
    driver = neo4j_driver()
    try:
        async with driver.session(
            database=os.environ.get("NEO4J_DATABASE", "neo4j"),
        ) as s:
            result = await asyncio.wait_for(
                s.run(_CONCEPT_CYPHER, {"name": name}),
                timeout=params.NEO4J_QUERY_TIMEOUT_S,
            )
            record = await asyncio.wait_for(
                result.single(), timeout=params.NEO4J_QUERY_TIMEOUT_S,
            )
    finally:
        await driver.close()
    if record is None or record["payload"] is None:
        return None
    return dict(record["payload"])
