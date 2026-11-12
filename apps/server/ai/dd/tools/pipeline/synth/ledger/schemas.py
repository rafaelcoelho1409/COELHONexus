"""What the model sends the ledger tools."""
from __future__ import annotations

from pydantic import BaseModel, Field


class BlockRef(BaseModel):
    hash: str = Field(description = "The hash of a code block of the ledger, as the ledger lists it.")


class VariantPair(BaseModel):
    first: str = Field(description = "The hash of one variant of an example.")
    second: str = Field(description = "The hash of another variant of the same example.")


class CodeQuery(BaseModel):
    query: str = Field(description = "Names the code must use or define, e.g. 'create_deep_agent StateBackend'.")
