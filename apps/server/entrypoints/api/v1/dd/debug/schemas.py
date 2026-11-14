"""HTTP request models for isolated DD ingestion benchmarks."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, model_validator

import domains


class TierBenchmarkBody(BaseModel):
    tier: Literal[1, 2, 3, 4]
    round_id: str = Field(
        min_length=1,
        max_length=48,
        pattern=domains.dd.ingestion.runtime.dispatch.params.BENCHMARK_ROUND_ID_PATTERN,
    )


class IngestionReplayBody(BaseModel):
    evaluation_round_id: str = Field(
        min_length=1,
        max_length=48,
        pattern=domains.dd.ingestion.runtime.dispatch.params.BENCHMARK_ROUND_ID_PATTERN,
    )
    page_indices: list[int] | None = Field(default=None, min_length=1, max_length=20)

    @model_validator(mode="after")
    def unique_page_indices(self):
        if self.page_indices is not None and len(set(self.page_indices)) != len(self.page_indices):
            raise ValueError("page_indices must not contain duplicates")
        if self.page_indices is not None and any(index < 0 for index in self.page_indices):
            raise ValueError("page_indices must be non-negative")
        return self
