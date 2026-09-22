"""Pure functions for the RR agent's graph_build tool — no I/O."""
from __future__ import annotations
from .... import entities

from datetime import date
from typing import Any


def dict_to_paper(d: dict[str, Any]) -> entities.NormalizedPaper:
    """Reverse the triage `_paper_as_dict` shape into NormalizedPaper.
    Tolerant — missing fields default to safe values."""
    published = d.get("published")
    pub_date = None
    if published:
        try:
            pub_date = date.fromisoformat(published)
        except (ValueError, TypeError):
            pub_date = None
    return entities.NormalizedPaper(
        arxiv_id              = d.get("arxiv_id"),
        title                 = d.get("title", "") or "",
        abstract              = d.get("abstract", "") or "",
        published             = pub_date,
        authors               = tuple(d.get("authors", []) or []),
        categories            = tuple(d.get("categories", []) or []),
        citations             = int(d.get("citations") or 0),
        influential_citations = int(d.get("influential_citations") or 0),
        hn_points             = int(d.get("hn_points") or 0),
        hn_num_comments       = int(d.get("hn_num_comments") or 0),
        hf_upvotes            = int(d.get("hf_upvotes") or 0),
        sources               = frozenset(d.get("sources") or ()),
    )
