"""What the ledger tools tell the model, as text."""
from __future__ import annotations
from . import params

from difflib import unified_diff

import domains


def unknown(digest: str) -> str:
    return f"No block {digest!r} in this chapter's ledger. Use a hash the ledger lists."


def describe(block: domains.dd.pipeline.ledger.entities.Block, family: domains.dd.pipeline.ledger.entities.Family) -> str:
    kind = "the canonical variant" if family.canonical == block.hash else "a variant"
    parses = {True: "parses", False: "does not parse", None: "not checked"}[block.parses]
    return (
        f"{block.hash}: {block.lang or 'text'}, {block.lines} lines, {parses}, score {block.score}, {kind}; "
        f"defines {', '.join(block.defines) or 'nothing'}; imports {', '.join(block.imports) or 'nothing'}; on {len(block.pages)} page(s)"
    )


def variants(ledger: domains.dd.pipeline.ledger.entities.Ledger, digest: str) -> str:
    """The family of `digest`: every variant of the example, best first, so the writer can see what the others add."""
    block = ledger.blocks.get(digest)
    if block is None:
        return unknown(digest)
    family = next(family for family in ledger.families if digest in family.hashes)
    lines = [f"{len(family.hashes)} variant(s) of this example, best first:"]
    lines.extend(f"- {describe(ledger.blocks[member], family)}" for member in family.hashes)
    return "\n".join(lines)


def block(ledger: domains.dd.pipeline.ledger.entities.Ledger, digest: str) -> str:
    """The code of one block, with its facts."""
    found = ledger.blocks.get(digest)
    if found is None:
        return unknown(digest)
    family = next(family for family in ledger.families if digest in family.hashes)
    return f"{describe(found, family)}\n```{found.lang}\n{found.body[:params.BLOCK_CHARS]}\n```"


def diff(ledger: domains.dd.pipeline.ledger.entities.Ledger, first: str, second: str) -> str:
    """What changes from one variant to the other."""
    for digest in (first, second):
        if digest not in ledger.blocks:
            return unknown(digest)
    changes = unified_diff(
        ledger.blocks[first].body.splitlines(), ledger.blocks[second].body.splitlines(), fromfile = first, tofile = second, lineterm = "", n = 1,
    )
    text = "\n".join(changes)
    return text[:params.DIFF_CHARS] if text else "The two blocks are the same code."


def search(ledger: domains.dd.pipeline.ledger.entities.Ledger, query: str) -> str:
    """The canonical blocks that use or define the names in `query`, best match first."""
    wanted = {name.lower() for name in domains.dd.pipeline.ledger.patterns.IDENTIFIER_RE.findall(query) if len(name) >= domains.dd.pipeline.ledger.params.MIN_IDENTIFIER_CHARS}
    if not wanted:
        return "Give at least one name to look for."
    found = []
    for family in ledger.families:
        candidate = ledger.blocks[family.canonical]
        names = {name.lower() for name in domains.dd.pipeline.ledger.domain.identifiers(candidate.body)}
        hits = len(wanted & names)
        if hits:
            found.append((hits, candidate))
    found.sort(key = lambda item: (-item[0], item[1].order))
    if not found:
        return f"No block in this chapter uses {', '.join(sorted(wanted))}."
    rows = []
    for hits, candidate in found[:params.SEARCH_RESULTS]:
        preview = " / ".join([line.strip() for line in candidate.body.splitlines() if line.strip()][:params.SEARCH_PREVIEW_LINES])
        rows.append(f"- {candidate.hash} ({candidate.lang or 'text'}, {candidate.lines} lines, matches {hits} of {len(wanted)} names): {preview}")
    return "\n".join(rows)
