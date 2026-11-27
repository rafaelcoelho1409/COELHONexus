"""Shared pure prompt composition for Distill retrieval specialists."""
from __future__ import annotations


def build_retrieval_system_prompt(instructions: str, schema_context: str) -> str:
    """Add the server-read source schema or policy to a specialist's instructions."""
    normalized_instructions = instructions.strip()
    normalized_schema = schema_context.strip()
    if not normalized_instructions or not normalized_schema:
        raise ValueError("retrieval instructions and source schema are required")
    return (
        f"{normalized_instructions}\n\n"
        "## Server-provided retrieval schema or policy\n"
        "Use this source contract before calling your retrieval tool. It describes the "
        "supported fields and boundaries; it does not authorize changing the server query.\n\n"
        f"{normalized_schema}"
    )
