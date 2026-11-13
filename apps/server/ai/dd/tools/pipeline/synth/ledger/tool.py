"""The tools a synth agent reads the chapter's code ledger with: the variants of an example, one block, the difference between two variants, and a
search by the names a block uses. The ledger is built by the node, so the agent works from the same facts the judge checks against."""
from __future__ import annotations
import domains
from . import schemas, service

from langchain_core.tools import StructuredTool


def ledger_tools(ledger: domains.dd.pipeline.ledger.entities.Ledger) -> list[StructuredTool]:
    """`list_variants`, `get_block`, `compare_variants` and `search_code` over `ledger`."""

    async def list_variants(hash: str) -> str:
        return service.variants(ledger, hash.strip())

    async def get_block(hash: str) -> str:
        return service.block(ledger, hash.strip())

    async def compare_variants(first: str, second: str) -> str:
        return service.diff(ledger, first.strip(), second.strip())

    async def search_code(query: str) -> str:
        return service.search(ledger, query)

    return [
        StructuredTool.from_function(
            coroutine = list_variants,
            name = "list_variants",
            description = "Lists every variant of one example in the chapter's code ledger, best first, with what each defines, imports and scores. Use it to pick the variant to teach.",
            args_schema = schemas.BlockRef,
        ),
        StructuredTool.from_function(
            coroutine = get_block,
            name = "get_block",
            description = "Returns the code of one block of the ledger, with its facts. Use it before you write about a block you have not read in full.",
            args_schema = schemas.BlockRef,
        ),
        StructuredTool.from_function(
            coroutine = compare_variants,
            name = "compare_variants",
            description = "Shows what changes from one variant of an example to another, as a diff. Use it to decide what a variant adds.",
            args_schema = schemas.VariantPair,
        ),
        StructuredTool.from_function(
            coroutine = search_code,
            name = "search_code",
            description = "Finds the blocks of the ledger that use or define the names you give, best match first. Use it to find the code for a step.",
            args_schema = schemas.CodeQuery,
        ),
    ]
