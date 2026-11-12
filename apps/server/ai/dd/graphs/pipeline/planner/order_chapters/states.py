"""State of the order_chapters subgraph: the parent keys it reads and writes, and what its steps hand to each other."""
from __future__ import annotations
from . import entities

import operator
import typing


class OrderState(typing.TypedDict, total = False):
    framework_slug: str
    thread_id: str
    chapter_plan_ref: str
    chapter_order_ref: str
    order_chapters_stats: dict

    # Handed from `prepare` to the samples and to `finalize`; small and JSON-able, as every checkpoint field should be.
    chapters: list[dict]
    composition: list[dict | None] | None
    source_ranking: list[int] | None
    homes: list[list[str] | None]
    cache_key: str
    started: float
    finished: bool            # the node was answered without asking the model (cache hit, trivial plan, unreadable outline)
    retry_samples: list[int]  # sample indexes asked again after a thin first round
    retry_done: bool
    samples: typing.Annotated[list[entities.OrderSample], operator.add]
