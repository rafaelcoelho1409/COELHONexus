"""HTTP request models for isolated DD ingestion benchmarks."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

import domains


class TierBenchmarkBody(BaseModel):
    tier: Literal[1, 2, 3, 4]
    round_id: str = Field(
        min_length=1,
        max_length=48,
        pattern=domains.dd.ingestion.runtime.dispatch.params.BENCHMARK_ROUND_ID_PATTERN,
    )
