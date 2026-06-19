"""Run an LLM-as-judge eval over a LangFuse dataset.

`run_dataset_eval(dataset_name, run_name, runner, judge)` walks every
item, runs `runner(item.input)` to produce an actual output, then
`judge(item.input, item.expected_output, actual)` to score it. Scores
attach to the named LangFuse dataset run.

Returns the number of items scored. Fails soft per item — one bad
item doesn't abort the run.

Pattern (offline / on-demand from a notebook or CLI):
    from infra.langfuse.datasets import run_dataset_eval
    from infra.langfuse.evals.judges.faithfulness import faithfulness

    async def runner(input_):
        # build the actual outline for this input — e.g. call planner
        return await build_planner_outline(input_)

    n = await run_dataset_eval(
        "dd.reference_book.v1",
        run_name = "production-2026-06-18",
        runner   = runner,
        judge    = faithfulness,
    )
"""
from __future__ import annotations

import logging
from typing import Awaitable, Callable

from ..client import get_client


logger = logging.getLogger(__name__)


async def run_dataset_eval(
    dataset_name: str,
    *,
    run_name: str,
    runner:   Callable[[dict], Awaitable[dict]],
    judge:    Callable[[dict, dict, dict], Awaitable[float]],
) -> int:
    """Score every dataset item with the given judge under `run_name`."""
    client = get_client()
    if client is None:
        logger.warning("[langfuse-datasets] client unavailable — run skipped")
        return 0
    try:
        dataset = client.get_dataset(dataset_name)
    except Exception as e:
        logger.warning(
            f"[langfuse-datasets] get_dataset({dataset_name!r}) failed: "
            f"{type(e).__name__}: {e}"
        )
        return 0

    n_scored = 0
    items = getattr(dataset, "items", None) or []
    for item in items:
        try:
            actual = await runner(item.input)
        except Exception as e:
            logger.warning(
                f"[langfuse-datasets] runner failed on item: "
                f"{type(e).__name__}: {e}"
            )
            continue
        try:
            score = await judge(item.input, item.expected_output, actual)
        except Exception as e:
            logger.warning(
                f"[langfuse-datasets] judge failed on item: "
                f"{type(e).__name__}: {e}"
            )
            continue
        try:
            # LangFuse v3 SDK: `item.run(run_name=...)` is a context manager
            # that returns a `LangfuseSpan` and binds the score to the
            # dataset-run-item automatically.
            if hasattr(item, "run"):
                with item.run(run_name = run_name) as span:
                    trace_id = getattr(span, "trace_id", None)
                    kwargs: dict = {"name": judge.__name__, "value": score}
                    if trace_id is not None:
                        kwargs["trace_id"] = trace_id
                    client.create_score(**kwargs)
            else:
                # Older SDK or stripped client — best effort.
                client.create_score(name = judge.__name__, value = score)
            n_scored += 1
        except Exception as e:
            logger.warning(
                f"[langfuse-datasets] score write failed: "
                f"{type(e).__name__}: {e}"
            )
    logger.info(
        f"[langfuse-datasets] eval {dataset_name!r}/{run_name!r}: "
        f"{n_scored}/{len(items)} scored"
    )
    return n_scored
