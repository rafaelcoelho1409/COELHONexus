"""Book-scoped cleanup across the BD retrieval projections."""
from __future__ import annotations

import asyncio

from . import elasticsearch_index, neo4j_index, qdrant_index


async def delete_book(book_id: str) -> dict[str, int]:
    """Remove all historical indexed data for a book from each retrieval store."""
    stores = (
        ("elasticsearch", elasticsearch_index.service.delete_book),
        ("qdrant", qdrant_index.service.delete_book),
        ("neo4j", neo4j_index.service.delete_book),
    )
    results = await asyncio.gather(
        *(delete_store(book_id) for _name, delete_store in stores),
        return_exceptions = True,
    )
    deleted: dict[str, int] = {}
    errors: dict[str, str] = {}
    for (name, _delete_store), result in zip(stores, results, strict = True):
        if isinstance(result, asyncio.CancelledError):
            raise result
        if isinstance(result, Exception):
            errors[name] = f"{type(result).__name__}: {result}"
        else:
            deleted[name] = result
    if errors:
        details = "; ".join(f"{name}: {error}" for name, error in errors.items())
        raise RuntimeError(f"BD book index cleanup was incomplete ({details})")
    return deleted
