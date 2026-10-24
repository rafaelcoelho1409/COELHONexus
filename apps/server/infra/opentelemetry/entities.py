"""Value objects and stateful entities this package manipulates.

Two unrelated concerns share this file because both are genuine "things",
not orchestration: the central metric registry (a plain value object per
instrument), and the log-dedup filter (a stateful entity with a real
invariant — see DedupeRateLimitFilter). Neither belongs in service.py,
which is I/O only.
"""
from __future__ import annotations
from . import domain, params

import logging
import time
from dataclasses import dataclass
from typing import Literal


@dataclass(frozen = True, slots = True)
class MetricSpec:
    """`name` is the wire name that lands in Mimir."""
    key:         str
    name:        str
    description: str
    kind:        Literal["counter", "histogram"]
    unit:        str = ""


INSTRUMENTS: tuple[MetricSpec, ...] = (
    MetricSpec(
        key         = "planner_run_duration",
        name        = "dd.planner_run_duration_seconds",
        description = "End-to-end planner run wall-clock duration",
        kind        = "histogram",
        unit        = "s",
    ),
    MetricSpec(
        key         = "planner_run_total",
        name        = "dd.planner_run_total",
        description = "Planner run count by outcome, mode, and framework",
        kind        = "counter",
    ),
    MetricSpec(
        key         = "planner_chapter_count",
        name        = "dd.planner_chapter_count",
        description = "Number of chapters emitted by planner runs",
        kind        = "histogram",
        unit        = "1",
    ),
    MetricSpec(
        key         = "ingestion_run_duration",
        name        = "dd.ingestion_run_duration_seconds",
        description = "End-to-end ingestion run wall-clock duration",
        kind        = "histogram",
        unit        = "s",
    ),
    MetricSpec(
        key         = "ingestion_run_total",
        name        = "dd.ingestion_run_total",
        description = "Ingestion run count by outcome, tier kind, and framework",
        kind        = "counter",
    ),
    MetricSpec(
        key         = "ingestion_output_files",
        name        = "dd.ingestion_output_files",
        description = "Post-processed output file count produced by ingestion",
        kind        = "histogram",
        unit        = "1",
    ),
    MetricSpec(
        key         = "ingestion_output_bytes",
        name        = "dd.ingestion_output_bytes",
        description = "Post-processed output bytes produced by ingestion",
        kind        = "histogram",
        unit        = "By",
    ),
    MetricSpec(
        key         = "chapter_synth_duration",
        name        = "dd.chapter_synth_duration_seconds",
        description = "Per-chapter synth wall-clock from start to accept/debt",
        kind        = "histogram",
        unit        = "s",
    ),
    MetricSpec(
        key         = "chapter_outcome",
        name        = "dd.chapter_outcome_total",
        description = ("Chapter outcomes — labels: outcome ∈ {accept, debt_below, "
                       "op12_rescue}; pinned_model; framework"),
        kind        = "counter",
    ),
    MetricSpec(
        key         = "refiner_iters",
        name        = "dd.refiner_iters_to_accept",
        description = "Number of Self-Refine iters before accept (or budget exhaustion)",
        kind        = "histogram",
        unit        = "1",
    ),
    MetricSpec(
        key         = "bucket_split_overflow",
        name        = "dd.bucket_split_overflow_total",
        description = ("Times Phase A.5 hit the section-count cap and merged "
                       "overflow into 'Additional'"),
        kind        = "counter",
    ),
    MetricSpec(
        key         = "grader_dim_score",
        name        = "dd.classical_grader_dim_score",
        description = ("Per-dim classical grader score (0-1) — labels: dim ∈ "
                       "{signal_to_noise, code_density, ...}"),
        kind        = "histogram",
        unit        = "1",
    ),
    MetricSpec(
        key         = "audit_missing_ratio",
        name        = "dd.audit_missing_hashes_ratio",
        description = "Per-iter ratio of missing vault hashes (0-1)",
        kind        = "histogram",
        unit        = "1",
    ),
    MetricSpec(
        key         = "study_completion_duration",
        name        = "dd.study_completion_seconds",
        description = "End-to-end study wall-clock (ingest → assembler)",
        kind        = "histogram",
        unit        = "s",
    ),
    MetricSpec(
        key         = "classical_patch_applied",
        name        = "dd.classical_patch_applied_total",
        description = "Phase 4 classical refiner patch applications — labels: dim",
        kind        = "counter",
    ),
    MetricSpec(
        key         = "ycs_ask_run_duration",
        name        = "ycs.ask_run_duration_seconds",
        description = "End-to-end YCS Ask wall-clock duration",
        kind        = "histogram",
        unit        = "s",
    ),
    MetricSpec(
        key         = "ycs_ask_run_total",
        name        = "ycs.ask_run_total",
        description = "YCS Ask run count by route, mode, and outcome",
        kind        = "counter",
    ),
    MetricSpec(
        key         = "ycs_retrieved_docs",
        name        = "ycs.retrieved_docs",
        description = "Documents retrieved before grading",
        kind        = "histogram",
        unit        = "1",
    ),
    MetricSpec(
        key         = "ycs_graded_docs",
        name        = "ycs.graded_docs",
        description = "Documents kept after grading",
        kind        = "histogram",
        unit        = "1",
    ),
    MetricSpec(
        key         = "ycs_rewrite_total",
        name        = "ycs.rewrite_total",
        description = "YCS query rewrite count",
        kind        = "counter",
    ),
    MetricSpec(
        key         = "ycs_subquestion_total",
        name        = "ycs.subquestion_total",
        description = "YCS deep-mode sub-question outcomes",
        kind        = "counter",
    ),
    MetricSpec(
        key         = "ycs_citation_count",
        name        = "ycs.citation_count",
        description = "Number of citations attached to YCS answers",
        kind        = "histogram",
        unit        = "1",
    ),
    MetricSpec(
        key         = "ycs_grounded_total",
        name        = "ycs.grounded_total",
        description = "Grounded YCS answers by route and mode",
        kind        = "counter",
    ),
    MetricSpec(
        key         = "rr_scan_run_duration",
        name        = "rr.scan_run_duration_seconds",
        description = "End-to-end Research Radar scan wall-clock duration",
        kind        = "histogram",
        unit        = "s",
    ),
    MetricSpec(
        key         = "rr_scan_run_total",
        name        = "rr.scan_run_total",
        description = "Research Radar scan count by outcome and degradation",
        kind        = "counter",
    ),
    MetricSpec(
        key         = "rr_findings",
        name        = "rr.findings",
        description = "Findings emitted per completed Research Radar scan",
        kind        = "histogram",
        unit        = "1",
    ),
    MetricSpec(
        key         = "rr_candidates",
        name        = "rr.candidates",
        description = "Candidates seen per completed Research Radar scan",
        kind        = "histogram",
        unit        = "1",
    ),
    MetricSpec(
        key         = "rr_theme_count",
        name        = "rr.theme_count",
        description = "Synthesis themes emitted per completed Research Radar scan",
        kind        = "histogram",
        unit        = "1",
    ),
    MetricSpec(
        key         = "rr_phase_event_total",
        name        = "rr.phase_event_total",
        description = "Research Radar phase event count by phase",
        kind        = "counter",
    ),
    MetricSpec(
        key         = "gen_ai_call_duration",
        name        = "gen_ai.call_duration_seconds",
        description = "LLM/embedding call wall-clock — labels: operation ∈ {chat, embedding}, model, outcome",
        kind        = "histogram",
        unit        = "s",
    ),
    MetricSpec(
        key         = "gen_ai_call_total",
        name        = "gen_ai.call_total",
        description = "LLM/embedding call count by operation, model, and outcome ∈ {ok, timeout, error}",
        kind        = "counter",
    ),
    MetricSpec(
        key         = "gen_ai_usage_tokens",
        name        = "gen_ai.usage_tokens",
        description = "Token usage per call — labels: operation, model, token_type ∈ {input, output}",
        kind        = "histogram",
        unit        = "1",
    ),
    MetricSpec(
        key         = "ycs_ingest_run_duration",
        name        = "ycs.ingest_run_duration_seconds",
        description = ("YCS extract/ingest pipeline run duration — labels: kind ∈ "
                       "{extract_videos, extract_channel, extract_playlist, embedding_migration}, outcome"),
        kind        = "histogram",
        unit        = "s",
    ),
    MetricSpec(
        key         = "ycs_ingest_run_total",
        name        = "ycs.ingest_run_total",
        description = "YCS extract/ingest pipeline run count by kind and outcome ∈ {ok, error}",
        kind        = "counter",
    ),
)


class DedupeRateLimitFilter(logging.Filter):
    """OTLP export-failure log-spam dampener. First-then-suppress per
    (logger, level, msg-prefix) preserves the initial degradation signal
    without flooding app logs when the collector is unreachable.

    A real invariant lives on the instance — a given key only passes once
    per `interval_s` window (tracked in `_last`) — which is why this is a
    class with a mutating method rather than a free function in domain.py.
    The key computation itself IS pure, so it's delegated to
    `domain.dedupe_key` instead of inlined here.
    """

    def __init__(self, interval_s: float = params.DEDUPE_LOG_INTERVAL_S):
        super().__init__()
        self._interval = interval_s
        self._last: dict = {}

    def filter(self, record: logging.LogRecord) -> bool:
        key = domain.dedupe_key(record)
        now = time.monotonic()
        prev = self._last.get(key)
        if prev is None or (now - prev) >= self._interval:
            self._last[key] = now
            return True
        return False


# One shared instance — service.py attaches it to every noisy OTel logger.
DEDUPE_LOG_FILTER = DedupeRateLimitFilter()
