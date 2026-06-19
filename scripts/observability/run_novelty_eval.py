"""RR novelty eval runner. Pure-math judge — no LLM call.

Each dataset item carries `prior_arxiv_ids` (history) and
`current_arxiv_ids` (what the digest produced this round). The runner
echoes `current_arxiv_ids` as the "actual" output; the judge computes
1 − Jaccard(prior ∩ current, current).

Useful sanity for the judge wiring + dataset run linkage; the production
hook would feed `current_arxiv_ids` from the live RR digest scan.

Run inside the FastAPI container:
    kubectl exec -i -n coelhonexus-dev <pod> -c coelhonexus-fastapi -- \\
        bash -c "PYTHONPATH=/app python /tmp/run_novelty_eval.py"
"""
from __future__ import annotations

import asyncio
import datetime
import logging


logging.basicConfig(level = logging.INFO, format = "%(levelname)s %(name)s: %(message)s")


DATASET_NAME = "rr.known_good_digest.v1"


async def rr_echo_runner(input_: dict) -> dict:
    """Echo current_arxiv_ids back as the digest output."""
    return {"arxiv_ids": input_.get("current_arxiv_ids", [])}


async def main() -> int:
    from infra.langfuse.datasets import run_dataset_eval
    from infra.langfuse.evals.judges.novelty import novelty

    run_name = f"novelty-smoke-{datetime.datetime.now(datetime.timezone.utc):%Y%m%d-%H%M%S}"
    n = await run_dataset_eval(
        DATASET_NAME,
        run_name = run_name,
        runner   = rr_echo_runner,
        judge    = novelty,
    )
    print(f"\n[novelty-eval] dataset={DATASET_NAME!r} run={run_name!r} scored={n}")
    return 0 if n > 0 else 1


if __name__ == "__main__":
    from infra.otel import init_otel
    init_otel()
    raise SystemExit(asyncio.run(main()))
