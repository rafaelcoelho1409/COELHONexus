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
