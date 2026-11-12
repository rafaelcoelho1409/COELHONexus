"""What the model sends the ledger tools."""
from __future__ import annotations

import pydantic


class BlockRef(pydantic.BaseModel):
    hash: str = pydantic.Field(description = "The hash of a code block of the ledger, as the ledger lists it.")


class VariantPair(pydantic.BaseModel):
    first: str = pydantic.Field(description = "The hash of one variant of an example.")
    second: str = pydantic.Field(description = "The hash of another variant of the same example.")


class CodeQuery(pydantic.BaseModel):
    query: str = pydantic.Field(description = "Names the code must use or define, e.g. 'create_deep_agent StateBackend'.")
