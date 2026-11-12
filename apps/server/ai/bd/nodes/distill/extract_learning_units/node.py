"""Thin graph wrappers for source preparation and learning extraction."""
from __future__ import annotations

import typing

from .... import graphs
from . import service


async def prepare_units(dependencies: graphs.distill.entities.DistillDependencies, state: dict[str, typing.Any]) -> dict:
    return await graphs.service.run_node(
        dependencies.progress_callback,
        "prepare_units",
        lambda: service.prepare_units(dependencies, state),
    )


async def extract_unit(dependencies: graphs.distill.entities.DistillDependencies, state: dict[str, typing.Any]) -> dict:
    async def _extract_batch() -> dict:
        completed = []
        for ref in state.get("unit_batch", []):
            output = await service.extract_unit(dependencies, {**state, "unit_ref": ref})
            completed.extend(output.get("completed_units", []))
        return {"completed_units": completed}

    return await graphs.service.run_node(
        dependencies.progress_callback,
        "extract_unit",
        _extract_batch,
    )
