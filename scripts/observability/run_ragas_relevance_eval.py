"""YCS ragas_relevance eval runner.

For each dataset item, attempt a real YCS Ask via in-cluster httpx to
`/api/v1/ycs/agents/search` (fast mode). When the cluster has no
ingested data (the answer will be ungrounded LLM output), the judge
still scores relevance against the reference answer.

Run inside the FastAPI container:
    kubectl exec -i -n coelhonexus-dev <pod> -c coelhonexus-fastapi -- \\
        bash -c "PYTHONPATH=/app python /tmp/run_ragas_relevance_eval.py"
"""
from __future__ import annotations

import asyncio
import datetime
import logging


logging.basicConfig(level = logging.INFO, format = "%(levelname)s %(name)s: %(message)s")


DATASET_NAME = "ycs.qa_pairs.v1"


async def ycs_ask_runner(input_: dict) -> dict:
    """Hit /api/v1/ycs/agents/search; on any failure return an empty answer
    so the judge scores 1 rather than the item being skipped silently."""
    import httpx
    payload = {
        "question":     input_.get("question", ""),
        "thread_id":    f"ragas-eval-{datetime.datetime.now(datetime.timezone.utc):%H%M%S}",
        "force_mode":   "fast",
        "channel_ids":  input_.get("channel_ids", []),
        "max_retries":  0,
    }
    try:
        async with httpx.AsyncClient(timeout = 60) as client:
            r = await client.post(
                "http://localhost:8000/api/v1/ycs/agents/search",
                json = payload,
            )
            r.raise_for_status()
            data = r.json()
            return {
                "answer":   data.get("answer", "") or "",
                "grounded": data.get("grounded", False),
                "sources":  data.get("retrieval_sources", []),
            }
    except Exception as e:
        logging.warning(f"[ragas-runner] /search failed: {type(e).__name__}: {e}")
        return {"answer": "", "grounded": False, "sources": []}


async def main() -> int:
    from infra.langfuse.datasets import run_dataset_eval
    from infra.langfuse.evals.judges.ragas_relevance import ragas_relevance

    run_name = f"ragas-smoke-{datetime.datetime.now(datetime.timezone.utc):%Y%m%d-%H%M%S}"
    n = await run_dataset_eval(
        DATASET_NAME,
        run_name = run_name,
        runner   = ycs_ask_runner,
        judge    = ragas_relevance,
    )
    print(f"\n[ragas-eval] dataset={DATASET_NAME!r} run={run_name!r} scored={n}")
    return 0 if n > 0 else 1


if __name__ == "__main__":
    from infra.otel import init_otel
    init_otel()
    raise SystemExit(asyncio.run(main()))
