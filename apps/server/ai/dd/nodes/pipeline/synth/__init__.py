"""The synth stage nodes, one package per node; the graph in `graphs.pipeline.synth` wires them in order.

Import order matters: a node's `domain` reads the packages it builds on at module level (`sawc` uses `render` and `sawc_derive`, `checklist` uses `sawc`), so the
packages load from the bottom up."""
from __future__ import annotations

from . import shared
from . import sawc_derive, render, outline, digest, sawc, checklist, mgsr, book_harmonize

__all__ = ["book_harmonize", "checklist", "digest", "mgsr", "outline", "render", "sawc", "sawc_derive", "shared"]
