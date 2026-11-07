"""Counting the pages the ingestion stages finish, for the run's progress."""
from __future__ import annotations
from . import entities


async def page_done(
    dependencies: entities.IngestionDependencies,
    node: str,
    total: int) -> None:
    """Count one page finished by `node` and report it; a normalized page is a finished page of the run."""
    if dependencies.progress is None:
        return
    tally = dependencies.tally
    async with tally.lock:
        tally.completed_by_node[node] += 1
        await dependencies.progress.update_agentic_node(
            node,
            current = tally.completed_by_node[node],
            total = total,
        )
        if node == "normalize_pages":
            tally.completed_pages += 1
            await dependencies.progress.update(current = tally.completed_pages)
