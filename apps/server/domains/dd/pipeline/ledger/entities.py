"""The code ledger of a chapter: every code block its pages carry, grouped into the examples they are variants of."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Block:
    """One distinct code block (identical fences on several pages are one block). `hash` is the vault hash, so a ledger entry is the `<code-ref>` the
    writers use. `role` is `code`, `setup` (an install line), `config` or `output`; only `code` is taught."""
    hash: str
    lang: str
    body: str
    lines: int
    pages: tuple[str, ...]
    order: int
    role: str
    parses: bool | None
    imports: tuple[str, ...]
    defines: tuple[str, ...]
    score: float


@dataclass(frozen=True, slots=True)
class Family:
    """Blocks that are variants of one example. `hashes` is best first; the first is the canonical variant the tutorial teaches."""
    hashes: tuple[str, ...]
    role: str
    pages: tuple[str, ...]

    @property
    def canonical(self) -> str:
        return self.hashes[0]


@dataclass(frozen=True, slots=True)
class Ledger:
    """What a chapter has to teach. `must_teach` is the canonical hash of every `code` family in the order the pages introduce them; `setup` holds
    the install lines (shown once, not taught)."""
    blocks: dict[str, Block]
    families: tuple[Family, ...]
    must_teach: tuple[str, ...]
    setup: tuple[str, ...]
    pages: tuple[str, ...]
