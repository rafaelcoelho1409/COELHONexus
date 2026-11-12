"""Thin graph wrapper for Learning Guide validation and publication."""
from __future__ import annotations

from typing import Any

from .... import graphs
from . import service


async def publish_report(dependencies: graphs.distill.entities.DistillDependencies, state: dict[str, Any]) -> dict:
    return await graphs.service.run_node(
        dependencies.progress_callback,
        "publish_report",
        lambda: service.publish_report(dependencies, state),
    )
