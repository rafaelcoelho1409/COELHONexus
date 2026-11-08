"""Planner-version selection and the stage names exposed to the web."""
from __future__ import annotations

import os


PLANNER_V2 = os.environ.get("DD_PLANNER_VERSION", "v2").strip().lower() == "v2"

NODE_ORDER_V1 = (
    "corpus_load",
    "off_topic",
    "doc_distill",
    "chapter_propose",
    "chapter_assign",
    "chapter_select",
    "order_chapters",
    "plan_write",
)
NODE_ORDER_V2 = (
    "corpus_load",
    "page_cards",
    "chapter_design",
    "chapter_select",
    "order_chapters",
    "plan_write",
)
NODE_ORDER = NODE_ORDER_V2 if PLANNER_V2 else NODE_ORDER_V1
