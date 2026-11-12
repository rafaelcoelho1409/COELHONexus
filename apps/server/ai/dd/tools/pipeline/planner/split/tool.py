"""The tools the chapter curator splits an oversize chapter with: cluster its units, and validate a partition."""
from __future__ import annotations
from . import schemas

import collections.abc

import langchain_core.tools


def cluster_units_tool(cluster: collections.abc.Callable[[int], list[list[int]] | None], describe: collections.abc.Callable[[list[int]], str]) -> langchain_core.tools.StructuredTool:
    """`cluster_units(n_parts)`: `cluster` returns the groups of unit ids (None when the units do not cluster), `describe` renders one group's units."""

    async def cluster_units(n_parts: int) -> str:
        groups = cluster(n_parts)
        if not groups:
            return "The units do not cluster into groups of the minimum size: partition them yourself by topic."
        return "\n\n".join(f"group {i} ({len(ids)} units):\n{describe(ids)}" for i, ids in enumerate(groups))

    return langchain_core.tools.StructuredTool.from_function(
        coroutine = cluster_units,
        name = "cluster_units",
        description = (
            "Groups the chapter's units by their paths, titles and summaries with no model and returns the groups with their "
            "units. Prefer its grouping; name each group from what its units teach."
        ),
        args_schema = schemas.ClusterInput,
    )


def validate_split_tool(check: collections.abc.Callable[[list[dict]], tuple[bool, str]]) -> langchain_core.tools.StructuredTool:
    """`validate_split(groups)`: `check` takes the proposed groups and returns (ok, reason)."""

    async def validate_split(groups: list[schemas.SplitGroupInput]) -> str:
        ok, reason = check([group.model_dump() for group in groups])
        return "The split is valid." if ok else f"The split is not valid: {reason}. Fix it and call validate_split again."

    return langchain_core.tools.StructuredTool.from_function(
        coroutine = validate_split,
        name = "validate_split",
        description = (
            "Checks a proposed split with no model: every unit in exactly one group, titles unique, concrete and unlike the "
            "other chapters', no group under the size floor. Answer only after it says the split is valid."
        ),
        args_schema = schemas.ValidateInput,
    )
