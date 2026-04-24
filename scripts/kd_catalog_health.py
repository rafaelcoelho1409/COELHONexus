#!/usr/bin/env python3
"""
KD Catalog Health Report — Tier 0d-5 (2026-04-23).

Reads the last N days of LangFuse synth spans, groups by `model` tag,
computes per-model:
  - total_calls
  - success_rate         (tool_call returned parseable output)
  - error_rate           (cascaded via exception — 4xx/5xx/timeout)
  - preservation_ratio   (avg across synth spans where the metadata
                          includes it; 0d-5 telemetry)

Prints a "demote these models" recommendation when any model in the
current `llm_chain.py` catalog exceeds the configured thresholds.

This is an analysis tool, not an online component. Runs ad-hoc from a
developer laptop or a weekly cron. Output is human-reviewed before any
catalog change.

Usage:
  export LANGFUSE_HOST="https://langfuse.YOUR_TAILNET_DOMAIN.ts.net"
  export LANGFUSE_PUBLIC_KEY="lf_pk_..."
  export LANGFUSE_SECRET_KEY="lf_sk_..."
  python scripts/kd_catalog_health.py [--days 7] [--min-calls 10]

Thresholds (tune via env):
  KD_CATALOG_MIN_SUCCESS_RATE   default 0.80
  KD_CATALOG_MIN_PRESERVATION   default 0.90
  KD_CATALOG_MAX_ERROR_RATE     default 0.30

Any model exceeding the min success/preservation thresholds OR the max
error rate gets a DEMOTE recommendation (with rationale).
"""
import argparse
import os
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone


def _load_langfuse_client():
    try:
        from langfuse import Langfuse
    except ImportError:
        print("ERROR: `langfuse` package not installed. pip install langfuse", file=sys.stderr)
        sys.exit(2)
    host = os.environ.get("LANGFUSE_HOST", "").strip()
    pk = os.environ.get("LANGFUSE_PUBLIC_KEY", "").strip()
    sk = os.environ.get("LANGFUSE_SECRET_KEY", "").strip()
    if not (pk and sk):
        print(
            "ERROR: LANGFUSE_PUBLIC_KEY + LANGFUSE_SECRET_KEY must be set.",
            file=sys.stderr,
        )
        sys.exit(2)
    return Langfuse(public_key=pk, secret_key=sk, host=host or None)


def _summarize(
    client,
    since: datetime,
    min_calls: int,
) -> dict[str, dict]:
    """Walk `synth`-tagged observations since `since`, group by model."""
    by_model: dict[str, dict] = defaultdict(lambda: {
        "calls": 0,
        "errors": 0,
        "preservation_samples": [],
    })
    # LangFuse API: fetch observations page-by-page. Use search on the
    # `synth` tag at the trace level, then walk the top-level generation.
    page = 1
    while True:
        resp = client.api.observations.get_many(
            page=page,
            limit=100,
            from_start_time=since.isoformat(),
            type="GENERATION",
        )
        data = getattr(resp, "data", None) or []
        if not data:
            break
        for obs in data:
            tags = list(getattr(obs, "tags", []) or [])
            # Only KD synth calls — adjust if you include grader/critic
            if not any(t == "synth" for t in tags):
                continue
            model_id = None
            for t in tags:
                if t.startswith("model:"):
                    model_id = t.split(":", 1)[1]
                    break
            if not model_id:
                meta = getattr(obs, "metadata", None) or {}
                model_id = meta.get("model")
            if not model_id:
                continue
            entry = by_model[model_id]
            entry["calls"] += 1
            level = getattr(obs, "level", None) or ""
            if level in {"ERROR", "WARNING"} or getattr(obs, "status_message", None):
                entry["errors"] += 1
            meta = getattr(obs, "metadata", None) or {}
            ratio = meta.get("preservation_ratio")
            if isinstance(ratio, (int, float)):
                entry["preservation_samples"].append(float(ratio))
        if len(data) < 100:
            break
        page += 1

    # Drop models with too few calls for a confident signal
    return {m: e for m, e in by_model.items() if e["calls"] >= min_calls}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--days", type=int, default=7, help="lookback window (default 7)")
    ap.add_argument("--min-calls", type=int, default=10, help="min calls per model to consider (default 10)")
    args = ap.parse_args()

    min_success_rate = float(os.environ.get("KD_CATALOG_MIN_SUCCESS_RATE", "0.80"))
    min_preservation = float(os.environ.get("KD_CATALOG_MIN_PRESERVATION", "0.90"))
    max_error_rate = float(os.environ.get("KD_CATALOG_MAX_ERROR_RATE", "0.30"))

    client = _load_langfuse_client()
    since = datetime.now(timezone.utc) - timedelta(days=args.days)

    print(f"LangFuse synth-span analysis — last {args.days} days from {since.isoformat()}")
    print(f"Min calls per model for inclusion: {args.min_calls}")
    print(f"Thresholds — success ≥ {min_success_rate}, preservation ≥ {min_preservation}, errors ≤ {max_error_rate}")
    print()

    by_model = _summarize(client, since, args.min_calls)
    if not by_model:
        print("No synth spans found with sufficient volume. Either Langfuse has no data yet or thresholds are too high.")
        return

    print(f"{'model':<55} {'calls':>7} {'success':>8} {'errors':>7} {'preservation':>13}")
    print("-" * 95)
    demote: list[tuple[str, list[str]]] = []
    for model_id in sorted(by_model.keys()):
        e = by_model[model_id]
        success_rate = (e["calls"] - e["errors"]) / e["calls"] if e["calls"] else 0.0
        error_rate = e["errors"] / e["calls"] if e["calls"] else 0.0
        pres_samples = e["preservation_samples"]
        pres = sum(pres_samples) / len(pres_samples) if pres_samples else float("nan")
        pres_str = f"{pres:.2f}" if pres_samples else "—"
        print(f"{model_id:<55} {e['calls']:>7} {success_rate:>8.2f} {error_rate:>7.2f} {pres_str:>13}")
        reasons = []
        if success_rate < min_success_rate:
            reasons.append(f"success {success_rate:.0%} < {min_success_rate:.0%}")
        if error_rate > max_error_rate:
            reasons.append(f"errors {error_rate:.0%} > {max_error_rate:.0%}")
        if pres_samples and pres < min_preservation:
            reasons.append(f"preservation {pres:.2f} < {min_preservation:.2f}")
        if reasons:
            demote.append((model_id, reasons))

    print()
    if demote:
        print(f"DEMOTE CANDIDATES ({len(demote)}):")
        for model_id, reasons in demote:
            print(f"  - {model_id}: {'; '.join(reasons)}")
        print()
        print("Edit apps/fastapi/services/llm_chain.py to remove or reorder these entries.")
        print("Re-run this script 7 days after the change to confirm improvement.")
    else:
        print("No demote candidates — catalog is healthy by current thresholds.")


if __name__ == "__main__":
    main()
