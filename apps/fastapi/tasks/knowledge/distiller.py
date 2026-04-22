"""
Knowledge Distiller — Celery Task

CONCEPT: Wraps the async LangGraph pipeline (graphs/knowledge/distiller.py)
into a sync Celery task. FastAPI returns immediately with a task_id; the
actual work runs for minutes in a separate worker process.

Pattern matches tasks/youtube/graph.py:
  - @app.task(bind=True, name="tasks.knowledge.distiller.*")
  - asyncio.run(inner_async_fn) bridges async graph into sync Celery
  - Dependencies built inside the task (no shared app.state — Celery workers
    don't run under FastAPI's lifespan)
  - Progress reported via self.update_state(state="PROGRESS", meta={...})

Dependencies (rebuilt per-task):
  - MinIOStudyStorage  — same bucket/endpoint as FastAPI lifespan
  - LLM fallback chain — same topology as app.py (Groq-first → NVIDIA NIM)
  - AsyncPostgresSaver — opened as a context manager; shared with FastAPI via
    the same connection string / thread_id=study_id

Queue routing: llm (same queue as tasks.youtube.neo4j.*). KD is dominated by
LLM calls — keeps it off the crawler and embedding queues.

Retries: none. KD is expensive (10-15 minutes of LLM calls per run). A failed
run should fail visibly; the user decides whether to retry. Matches the
long-task convention already used by ingest_to_neo4j.
"""
import asyncio
import os
import sys
if "/app" not in sys.path:
    sys.path.insert(0, "/app")

from urllib.parse import quote_plus as _urlencode

from celery_app import app
from celery.utils.log import get_task_logger

logger = get_task_logger(__name__)


def _build_llm_chain():
    """
    Build the shared Groq + NVIDIA NIM fallback chain from services/llm_chain.py.
    Celery workers can't read app.state, so we call the same builder here —
    this keeps the two entry points (FastAPI lifespan + Celery tasks) in sync.

    Uses longer NIM timeouts than the HTTP-path default because KD synthesizer
    calls with 262K-context reasoning models can legitimately take minutes.
    """
    from services.llm_chain import build_llm_fallback_chain
    # Celery tasks run in the background (quality-first, wall-time isn't the
    # priority). Give reasoning models (Kimi K2.5, GLM-5.1, Qwen3.5-397B) full
    # room to complete their chain-of-thought on ~25K-token planner prompts
    # before cascading to lower-quality fallbacks.
    # 2026-04-20 tuning: 420s NIM (7 min) absorbs p99 latency for 262K-ctx
    # reasoning models; 120s Groq is safety padding (Groq p99 << 60s in practice).
    return build_llm_fallback_chain(groq_timeout_s = 120, nim_timeout_s = 420)


def _pg_url() -> str:
    """Build the same PostgreSQL URL FastAPI uses for its checkpointer."""
    pg_host = os.environ.get("POSTGRES_HOST", "postgresql.postgresql.svc.cluster.local")
    pg_port = os.environ.get("POSTGRES_PORT", "5432")
    pg_user = os.environ.get("POSTGRES_USER", "postgres")
    pg_password = os.environ.get("POSTGRES_PASSWORD", "")
    pg_database = os.environ.get("POSTGRES_DATABASE", "coelhonexus")
    return f"postgresql://{pg_user}:{_urlencode(pg_password)}@{pg_host}:{pg_port}/{pg_database}"


@app.task(
    bind = True,
    name = "tasks.knowledge.distiller.run_knowledge_distiller",
)
def run_knowledge_distiller(
    self,
    study_id: str,
    framework: str,
    version: str | None = None,
    docs_url: str | None = None,
    language: str | None = None,
    user_id: str = "default",
    user_profile: dict | None = None,
    study_root: str | None = None,
    max_concurrent_chapters: int = 5,
    # Resolver hints (all optional; forwarded by the /studies router when
    # the call originates from a prior /resolve — None on legacy paths)
    tier: int | None = None,
    github_discover: str | None = None,
    github_org: str | None = None,
    github_repo: str | None = None,
    github_default_branch: str | None = None,
    repo_url: str | None = None,
) -> dict:
    """
    Run the full Knowledge Distiller pipeline for one framework.

    Inputs must be JSON-serializable (Celery serializes over Redis):
      - study_id: stable UUID; also used as the LangGraph thread_id so
        checkpoint state is recoverable via /studies/{study_id}/status.
      - framework, version, docs_url, language: passed through to the graph.
      - user_id: multi-tenancy key (MinIO prefix).
      - user_profile: dict form of schemas.knowledge.inputs.UserProfile.
        Reconstructed to a Pydantic model inside _run().
      - study_root: pre-computed MinIO key prefix
        ("{user_id}/knowledge/{framework}-{version}-{ts}"). The router
        builds this so /studies/{id} can list artifacts without guessing.

    Progress reporting: after each LangGraph superstep a meta update is
    emitted with the completed node name and the resulting phase. Flower
    and GET /tasks/{id} surface this live.

    Returns a summary dict (JSON-serializable):
      {
        "study_id": ...,
        "study_root": ...,
        "phase": "complete" | "failed",
        "ingest_tier_used": "llms-full-txt" | ...,
        "num_chapters": int,
        "summary_path": str | None,
        "debt_path": str | None,
        "validation_report": dict | None,
      }
    """
    if not study_root:
        raise ValueError("study_root is required — the router must precompute it")
    if user_profile is None:
        user_profile = {}

    logger.info(
        f"[KD:{study_id}] starting — framework={framework} "
        f"language={language or '-'} user_id={user_id} study_root={study_root}"
    )
    self.update_state(
        state = "PROGRESS",
        meta = {
            "study_id": study_id,
            "study_root": study_root,
            "phase": "ingest",
            "last_node": None,
            "nodes_seen": 0,
        },
    )

    async def _run():
        from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
        from services.knowledge.cache import StudyCache
        from services.knowledge.storage import MinIOStudyStorage
        from schemas.knowledge.inputs import UserProfile
        from graphs.knowledge.distiller import KnowledgeDistillerGraph

        # ---------------------------------------------------------------
        # Dependencies — MinIO, cache, LLM, Postgres
        # ---------------------------------------------------------------
        storage = MinIOStudyStorage(
            bucket = os.environ.get("MINIO_BUCKET_COELHONEXUS", "coelhonexus"),
            endpoint_url = os.environ.get("MINIO_ENDPOINT", "https://minio-api.YOUR_TAILNET_DOMAIN.ts.net"),
            access_key = os.environ.get("AWS_ACCESS_KEY_ID", ""),
            secret_key = os.environ.get("AWS_SECRET_ACCESS_KEY", ""),
        )
        # ensure_bucket is idempotent — also called from FastAPI lifespan.
        await storage.ensure_bucket()

        # Cache shares the same MinIO bucket (separate `_cache/` prefix).
        # TTL for "latest" entries defaults to 14 days; pinned versions
        # are immutable.
        cache = StudyCache(storage = storage, latest_ttl_days = 14)

        # Three LLM chains with distinct policies:
        #   llm         — main fallback chain (14 models). Used for the
        #                 planner, critic, assembler — LLM-as-judge-class work.
        #   synth_llm   — synth-only chain EXCLUDING the Groq tail
        #                 (llama-3.3-70b and llama-3.1-8b). The tail has
        #                 documented 32% code-gen error rates and degrades
        #                 structured-output quality; we refuse to ship a
        #                 chapter produced by them.
        #   curator_llm — pinned to GLM-5.1 (one model). Normalizes tone
        #                 across all chapters at the end. Rotating the
        #                 curator defeats its purpose.
        from services.llm_chain import (
            build_curator_llm,
            build_synth_fallback_chain,
        )
        llm = _build_llm_chain()
        synth_llm = build_synth_fallback_chain(
            groq_timeout_s = 120, nim_timeout_s = 420,
        )
        curator_llm = build_curator_llm(timeout_s = 600)

        # ---------------------------------------------------------------
        # Build graph with checkpointer and stream updates
        # ---------------------------------------------------------------
        async with AsyncPostgresSaver.from_conn_string(_pg_url()) as checkpointer:
            await checkpointer.setup()

            graph = KnowledgeDistillerGraph().build_knowledge_distiller_graph(
                llm = llm,
                storage = storage,
                cache = cache,
                synth_llm = synth_llm,
                curator_llm = curator_llm,
                checkpointer = checkpointer,
                max_concurrent_chapters = max_concurrent_chapters,
            )

            initial_state = {
                "framework": framework,
                "version": version,
                "docs_url": docs_url,
                "language": language,
                "user_id": user_id,
                "user_profile": UserProfile(**user_profile),
                "study_root": study_root,
                # Resolver hints — forwarded to the ingest node so the
                # dispatcher can pick the right tier without re-probing.
                "tier": tier,
                "github_discover": github_discover,
                "github_org": github_org,
                "github_repo": github_repo,
                "github_default_branch": github_default_branch,
                "repo_url": repo_url,
                "current_phase": "ingest",
                "ingest_tier_used": "none",
                "raw_files": [],
                "manifest": [],
                "plan": [],
                "synthesis_results": [],
                "validation_report": None,
                "summary_path": None,
                "debt_path": None,
            }

            config = {
                "configurable": {"thread_id": study_id},
                "recursion_limit": 100,
            }

            nodes_seen = 0
            latest_phase = "ingest"
            latest_node = None

            # stream_mode="updates" yields {"node_name": {"state_key": value}}
            # per completed node. Multiple nodes may complete in parallel
            # (synthesize_chapter during fan-out) — we report each separately.
            async for chunk in graph.astream(
                initial_state,
                config = config,
                stream_mode = "updates",
            ):
                for node_name, update in chunk.items():
                    nodes_seen += 1
                    latest_node = node_name
                    if isinstance(update, dict):
                        new_phase = update.get("current_phase")
                        if new_phase:
                            latest_phase = new_phase
                    logger.info(
                        f"[KD:{study_id}] node={node_name} phase={latest_phase} "
                        f"(superstep chunk #{nodes_seen})"
                    )
                    self.update_state(
                        state = "PROGRESS",
                        meta = {
                            "study_id": study_id,
                            "study_root": study_root,
                            "phase": latest_phase,
                            "last_node": latest_node,
                            "nodes_seen": nodes_seen,
                        },
                    )

            # Pull the final checkpointed state — astream doesn't yield it back
            # after completion. aget_state reads whatever the checkpointer wrote
            # on the final superstep.
            snapshot = await graph.aget_state(config)
            final = snapshot.values if snapshot else {}

            return {
                "study_id": study_id,
                "study_root": study_root,
                "phase": final.get("current_phase", latest_phase),
                "ingest_tier_used": final.get("ingest_tier_used"),
                "num_chapters": len(final.get("synthesis_results") or []),
                "summary_path": final.get("summary_path"),
                "debt_path": final.get("debt_path"),
                "validation_report": final.get("validation_report"),
            }

    try:
        result = asyncio.run(_run())
    except Exception as e:
        logger.exception(f"[KD:{study_id}] failed: {e}")
        # Re-raise — Celery will mark the task FAILURE and record the
        # traceback. The router can surface it via /tasks/{id}.
        raise

    logger.info(
        f"[KD:{study_id}] done — phase={result.get('phase')} "
        f"chapters={result.get('num_chapters')} "
        f"summary={result.get('summary_path')}"
    )
    return result
