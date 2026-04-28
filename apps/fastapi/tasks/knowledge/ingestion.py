"""
Knowledge Distiller — Standalone Ingestion Celery Task

Runs ONLY the ingestion stage: fetch + parse + persist the framework's
docs to MinIO and populate the corpus cache. No planning, no synthesis,
no critic — that's run_knowledge_distiller's job.

Used by POST /api/v1/knowledge/ingestion. Mirrors the YouTube ingest
pattern: enqueue, poll task_id, get result.

Cache contract:
  - Output lands at `_cache/ingestion/{framework_slug}/{version_slug}/`
    (managed by services.knowledge.cache.StudyCache).
  - A study_root copy lives at `{user_id}/knowledge/{framework}-{version}/research/raw/`
    so the artifacts are inspectable per-call. The downstream /studies
    flow re-reads from the cache, not from this study_root, so the
    duplicate working copy is incidental.
"""
import asyncio
import os
import sys

if "/app" not in sys.path:
    sys.path.insert(0, "/app")

from celery_app import app
from celery.utils.log import get_task_logger

logger = get_task_logger(__name__)


@app.task(
    bind = True,
    name = "tasks.knowledge.ingestion.run_knowledge_ingestion",
    # acks_late=False mirrors the distiller task: ingestion is non-idempotent
    # (MinIO writes, network fetches), user-initiated, and resumable via the
    # ingest cache. Auto-redelivery after worker death silently re-runs an
    # abandoned ingest — expensive on Tier-4 Playwright crawls. The user
    # reposts on FAILURE instead.
    acks_late = False,
)
def run_knowledge_ingestion(
    self,
    study_id: str,
    framework: str,
    version: str | None = None,
    docs_url: str | None = None,
    language: str | None = None,
    user_id: str = "default",
    study_root: str | None = None,
    tier: int | None = None,
    github_discover: str | None = None,
    github_org: str | None = None,
    github_repo: str | None = None,
    github_default_branch: str | None = None,
    repo_url: str | None = None,
) -> dict:
    """
    Ingest a framework's docs into MinIO + populate the corpus cache.

    Caller (`POST /api/v1/knowledge/ingestion`) checks the cache first and
    only enqueues this task on a miss (or `force=True`). The task itself
    re-checks the cache via `ingest_framework_docs` for partial-resume
    semantics in case a previous worker crashed mid-run.

    Returns a summary dict with manifest stats — caller polls
    `/api/v1/tasks/{task_id}` to retrieve it.
    """
    self.update_state(
        state = "PROGRESS",
        meta = {
            "study_id": study_id,
            "study_root": study_root,
            "phase": "ingest",
            "framework": framework,
            "version": version or "latest",
        },
    )

    async def _run():
        from services.knowledge.cache import StudyCache, compute_manifest_hash
        from services.knowledge.storage import MinIOStudyStorage
        from services.knowledge.ingestion import ingest_framework_docs
        from schemas.knowledge.ingestion import DocsIngestionConfig
        from graphs.knowledge.helpers import _write_manifest_json

        storage = MinIOStudyStorage(
            bucket = os.environ.get("MINIO_BUCKET_COELHONEXUS", "coelhonexus"),
            endpoint_url = os.environ.get(
                "MINIO_ENDPOINT", "https://minio-api.YOUR_TAILNET_DOMAIN.ts.net",
            ),
            access_key = os.environ.get("AWS_ACCESS_KEY_ID", ""),
            secret_key = os.environ.get("AWS_SECRET_ACCESS_KEY", ""),
        )
        await storage.ensure_bucket()
        cache = StudyCache(storage = storage, latest_ttl_days = 14)

        cfg = DocsIngestionConfig(
            framework = framework,
            version = version,
            docs_url = docs_url,
            language = language,
            study_root = study_root,
            study_id = study_id,
            tier = tier,
            github_discover = github_discover,
            github_org = github_org,
            github_repo = github_repo,
            github_default_branch = github_default_branch,
        )
        result = await ingest_framework_docs(cfg, storage, cache = cache)
        await _write_manifest_json(storage, study_root, result.manifest)
        slugs = [m.slug for m in result.manifest]
        manifest_hash = compute_manifest_hash(slugs)
        return result, manifest_hash

    try:
        ingest_result, manifest_hash = asyncio.run(_run())
    except Exception as e:
        logger.error(
            f"[KD-ingest:{study_id}] failed — "
            f"framework={framework} version={version or 'latest'}: "
            f"{type(e).__name__}: {e}"
        )
        raise

    logger.info(
        f"[KD-ingest:{study_id}] done — tier={ingest_result.tier_used} "
        f"files={ingest_result.total_files} bytes={ingest_result.total_bytes} "
        f"manifest_hash={manifest_hash}"
    )

    return {
        "study_id": study_id,
        "study_root": study_root,
        "framework": framework,
        "version": version or "latest",
        "phase": "complete",
        "tier_used": ingest_result.tier_used,
        "total_files": ingest_result.total_files,
        "total_bytes": ingest_result.total_bytes,
        "manifest_hash": manifest_hash,
        "manifest_path": f"{study_root}/research/manifest.json",
    }
