"""State of the off_topic subgraph."""
from __future__ import annotations
from . import entities

import operator
import typing


class OffTopicState(typing.TypedDict, total = False):
    framework_slug: str
    thread_id: str
    raw_files: list[str]
    corpus_stats: dict
    relevant_files: list[str]
    off_topic_stats: dict

    finished: bool                  # nothing to judge (no pages): the node answered without the steps below
    framework_name: str
    category: str
    index_summary: str
    groups: dict[str, list[int]]    # digest of the judged text → indexes of the pages that share it
    reps: dict[str, str]            # digest → the key of one page that has it
    lengths: dict[str, int]         # digest → characters of the judged text
    cached: dict[str, dict]         # digest → verdict already stored by an earlier run ({"keep", "final"})
    auto_keep: list[int]            # reference sub-pages: on-topic by construction, never judged
    todo: list[str]                 # digests still to judge
    recheck: list[str]              # digests of confident DROPs of substantial pages, judged a second time
    rechecked: bool
    started: float
    verdicts: typing.Annotated[list[entities.Verdict], operator.add]
