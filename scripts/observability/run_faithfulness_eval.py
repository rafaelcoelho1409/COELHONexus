"""Eval runner — for each dataset item, fetch the LangFuse-managed
`dd.planner.chapter_propose` template, render it with the gold input,
call the rotator's `chat_judge_async` to get an actual outline, and
score it with the `faithfulness` judge. Scores attach to the named
LangFuse dataset run.

Run inside the FastAPI container:
    kubectl exec -i -n coelhonexus-dev <fastapi-pod> -c coelhonexus-fastapi -- \\
        bash -c "PYTHONPATH=/app python /tmp/run_faithfulness_eval.py"
"""
from __future__ import annotations

import asyncio
import datetime
import json
import logging
import sys


logging.basicConfig(
    level  = logging.INFO,
    format = "%(levelname)s %(name)s: %(message)s",
)


DATASET_NAME  = "dd.reference_book.v1"
PROMPT_NAME   = "dd.planner.chapter_propose"
PROMPT_LABEL  = "production"


def _planner_variables(input_: dict) -> dict:
    """Map a dataset item's `input` into the get_prompt variable dict."""
    return {
        "framework":        input_.get("framework", ""),
        "target_chapters":  input_.get("target_chapters", 5),
        "n_source_keys":    len(input_.get("source_keys", [])),
        "proposals_min":    3,
        "proposals_max":    8,
        "title_min_words":  2,
        "title_max_words":  6,
        "concepts_min":     3,
        "concepts_max":     8,
        "headings_block":   "(none)",
        "namespaces_block": "(none)",
        "corpus_label":     "DOC SUMMARIES",
        "corpus_block":     input_.get("summary", "") or "(no summary provided)",
    }


async def planner_runner(input_: dict) -> dict:
    """Mini end-to-end DD planner: LangFuse template + rotator call."""
    from infra.langfuse.prompts import get_prompt
    from domains.llm.rotator.chain import chat_judge_async, init_dynamic_catalog

    try:
        await init_dynamic_catalog()
    except Exception:
        pass

    rendered = get_prompt(
        PROMPT_NAME, label = PROMPT_LABEL, variables = _planner_variables(input_),
    )
    if not rendered:
        return {"chapters": []}

    raw = await chat_judge_async(rendered, max_tokens = 2048, temperature = 0.2)
    try:
        data = json.loads(raw)
        return {"chapters": data.get("proposals", [])}
    except Exception:
        return {"chapters": [], "_raw": raw[:500]}


async def main() -> int:
    from infra.langfuse.datasets import run_dataset_eval
    from infra.langfuse.evals.judges.faithfulness import faithfulness

    run_name = f"smoke-eval-{datetime.datetime.now(datetime.timezone.utc):%Y%m%d-%H%M%S}"
    n = await run_dataset_eval(
        DATASET_NAME,
        run_name = run_name,
        runner   = planner_runner,
        judge    = faithfulness,
    )
    print(f"\n[eval] dataset={DATASET_NAME!r} run={run_name!r} scored={n}")
    return 0 if n > 0 else 1


if __name__ == "__main__":
    # Initialize OTel so any spans we emit during the runner land in LangFuse.
    from infra.otel import init_otel
    init_otel()
    raise SystemExit(asyncio.run(main()))
