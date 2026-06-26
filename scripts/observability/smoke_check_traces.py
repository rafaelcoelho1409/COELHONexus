"""Observability smoke check — query the latest DD / YCS / RR traces from
LangFuse API and fail if the contract is broken.

Checks per pipeline:
  1. A recent workflow-root trace exists (within --lookback-hours)
  2. Trace-level input AND output are non-null
  3. At least one gen_ai.chat observation is present

Exit 0 = all pipelines pass. Exit 1 = at least one check failed.

Run inside the FastAPI container (LangFuse credentials available):

    kubectl exec -i -n coelhonexus-dev <fastapi-pod> -c coelhonexus-fastapi -- \\
        bash -c "PYTHONPATH=/app python /app/scripts/observability/smoke_check_traces.py"

Or with a custom lookback:

    ... smoke_check_traces.py --lookback-hours 48
"""
from __future__ import annotations

import argparse
import logging
import os
import sys
from datetime import datetime, timedelta, timezone


logging.basicConfig(
    level   = logging.INFO,
    format  = "%(asctime)s [%(levelname)s] %(message)s",
    datefmt = "%H:%M:%S",
)
logger = logging.getLogger(__name__)


_PIPELINE_ROOT_SPANS: dict[str, str] = {
    "dd_planner": "dd.planner.run",
    "dd_synth":   "dd.synth.study.run",
    "ycs":        "ycs.ask.run",
    "rr":         "rr.scan.run",
}

_GENAI_SPAN_NAME = "gen_ai.chat"


def _get_client():
    try:
        from langfuse import Langfuse
        return Langfuse(
            public_key  = os.environ.get("LANGFUSE_PUBLIC_KEY",  ""),
            secret_key  = os.environ.get("LANGFUSE_SECRET_KEY",  ""),
            host        = os.environ.get("LANGFUSE_HOST", "http://langfuse-web.langfuse.svc.cluster.local:3000"),
        )
    except Exception as e:
        logger.error(f"LangFuse client init failed: {e}")
        return None


def _find_trace_for_pipeline(
    client,
    pipeline_name: str,
    span_name: str,
    cutoff: datetime,
) -> dict | None:
    """Return the most-recent trace for this pipeline after `cutoff`, searching by span name."""
    try:
        page = client.api.trace.list(
            name       = span_name,
            limit      = 5,
            from_timestamp = cutoff.isoformat(),
        )
        items = getattr(page, "data", None) or []
        if items:
            return vars(items[0]) if not isinstance(items[0], dict) else items[0]
    except Exception as e:
        logger.warning(f"[{pipeline_name}] trace list failed: {e}")
    return None


def _check_io(trace: dict, pipeline: str) -> tuple[bool, list[str]]:
    """Fail if both input AND output are null/empty on the trace."""
    issues = []
    inp = trace.get("input") or trace.get("inputCost") or trace.get("inputTokens")
    out = trace.get("output") or trace.get("outputCost") or trace.get("outputTokens")
    if not inp:
        issues.append("trace.input is null")
    if not out:
        issues.append("trace.output is null")
    return (len(issues) == 0), issues


def _check_genai_observations(client, trace_id: str, pipeline: str) -> tuple[bool, list[str]]:
    """Fail if no gen_ai.chat observations exist under the trace."""
    try:
        obs_page = client.api.observations.get_many(
            trace_id = trace_id,
            name     = _GENAI_SPAN_NAME,
            limit    = 1,
        )
        items = getattr(obs_page, "data", None) or []
        if not items:
            return False, [f"no '{_GENAI_SPAN_NAME}' observations found"]
        return True, []
    except Exception as e:
        return False, [f"observation query failed: {e}"]


def run_smoke_check(lookback_hours: int) -> bool:
    client = _get_client()
    if client is None:
        return False

    cutoff = datetime.now(tz=timezone.utc) - timedelta(hours=lookback_hours)
    all_passed = True

    for pipeline, span_name in _PIPELINE_ROOT_SPANS.items():
        logger.info(f"[{pipeline}] checking (span={span_name!r}, lookback={lookback_hours}h)")
        trace = _find_trace_for_pipeline(client, pipeline, span_name, cutoff)
        if trace is None:
            logger.error(f"[{pipeline}] FAIL — no recent trace found within {lookback_hours}h")
            all_passed = False
            continue

        trace_id = trace.get("id") or trace.get("traceId", "")
        logger.info(f"[{pipeline}] found trace {trace_id}")

        ok_io, io_issues = _check_io(trace, pipeline)
        if not ok_io:
            for issue in io_issues:
                logger.error(f"[{pipeline}] FAIL — {issue}")
            all_passed = False
        else:
            logger.info(f"[{pipeline}] trace input/output: OK")

        ok_genai, genai_issues = _check_genai_observations(client, trace_id, pipeline)
        if not ok_genai:
            for issue in genai_issues:
                logger.error(f"[{pipeline}] FAIL — {issue}")
            all_passed = False
        else:
            logger.info(f"[{pipeline}] gen_ai.chat observations: OK")

    if all_passed:
        logger.info("All pipeline smoke checks PASSED")
    else:
        logger.error("One or more pipeline smoke checks FAILED")
    return all_passed


def main() -> int:
    parser = argparse.ArgumentParser(description="LangFuse observability smoke check")
    parser.add_argument(
        "--lookback-hours", type=int, default=24,
        help="Hours to look back for recent traces (default: 24)",
    )
    parser.add_argument(
        "--pipeline", choices=list(_PIPELINE_ROOT_SPANS), default=None,
        help="Check a single pipeline instead of all",
    )
    args = parser.parse_args()

    global _PIPELINE_ROOT_SPANS
    if args.pipeline:
        _PIPELINE_ROOT_SPANS = {args.pipeline: _PIPELINE_ROOT_SPANS[args.pipeline]}

    passed = run_smoke_check(args.lookback_hours)
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
