"""Plain @dataclass value objects for the RR domain.

Per docs/CODE-CONVENTIONS.md §entities: frozen-and-slots value objects
carrying SHAPES between phases. No Pydantic here — Pydantic stays at the
HTTP/MCP boundary. No I/O. Hashable so they slot into sets/frozensets.

Phase contracts (architecture doc §3):

  NormalizedPaper   discovery → triage → graph_build
                    The unified shape after cross-source normalization +
                    dedup. Source-specific Paper/Hit shapes in fastmcp/
                    collapse here at the orchestrator phase boundary.

  Extraction        deep_read → synthesis (via state.fs[arxiv_id])
                    The per-paper deep-read output. Kept out of the
                    orchestrator's LLM context via the virtual FS.

  Finding           report → SSE / Postgres / MinIO digest.json
                    The assembled digest item — what the user sees.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date


@dataclass(frozen=True, slots=True)
class NormalizedPaper:
    """The unified shape after cross-source normalize() + dedup_by_arxiv_id.

    `arxiv_id` is the canonical id (version stripped — `2406.12345`, never
    `2406.12345v2`). None means the source couldn't surface an arxiv_id
    (e.g. an HN story whose URL doesn't point at arxiv.org / hf papers);
    such papers can't dedup cross-source and stand alone in the candidate
    list.

    Per-source signal fields hold ONE source's value each (no double-
    counting). Cross-source dedup merges by MAX — only the source with
    that signal will have a non-zero value, so max collapses correctly.
    """
    # Identity
    arxiv_id:                str | None
    title:                   str
    abstract:                str
    published:               date | None

    # Authorship / classification
    authors:                 tuple[str, ...]  = ()
    categories:              tuple[str, ...]  = ()

    # Per-source signals (merged by max at dedup time)
    citations:               int  = 0           # S2 citation_count
    influential_citations:   int  = 0           # S2 influential_citation_count
    hn_points:               int  = 0           # HN upvotes
    hn_num_comments:         int  = 0           # HN discussion depth
    hf_upvotes:              int  = 0           # HF Daily Papers community upvotes

    # Provenance — which sources surfaced this paper (frozenset ⊆ SOURCES_ALL)
    sources:                 frozenset[str]    = field(default_factory=frozenset)

    # Computed later (post-graph_build)
    embedding:               tuple[float, ...] | None  = None
    has_code:                bool                       = False     # papers_with_code (v2)


@dataclass(frozen=True, slots=True)
class Extraction:
    """One paper's deep-read output. Written to `state.fs[arxiv_id]` by
    the deep_read subagent; read by synthesis and report.

    Fields trade off detail vs context-cost — the orchestrator never
    sees these contents, only the list of arxiv_id keys.
    """
    arxiv_id:       str
    problem:        str         # 2-3 sentences: what real-world gap does this close
    method:         str         # 4-6 sentences: how does the paper do it
    math:           str         # key formulas (LaTeX) + their role in the method
    how_to_build:   str         # implementation notes — what to wire to what
    money_angle:    str         # commercial / portfolio applicability
    confidence:     float = 0.0 # self-rated extraction confidence in [0, 1]


@dataclass(frozen=True, slots=True)
class Finding:
    """One ranked digest item. Persisted in `radar_findings` + emitted to
    FastHTML via SSE."""
    arxiv_id:       str
    rank:           int                   # 1..N within this scan's digest
    signal:         float                 # computed via signal_score
    title:          str
    authors:        tuple[str, ...]
    summary:        str                   # 1-line "what's new" for the card
    extraction:     Extraction | None     # None when deep_read failed for this paper
    is_new:         bool                  # not in radar_seen before this scan
    themes:         tuple[str, ...]   = ()     # from synthesis subagent's clustering
    sources:        frozenset[str]    = field(default_factory=frozenset)
