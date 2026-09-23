"""LLM-as-judge evals — offline evaluation suite: gold corpora under
`datasets/`, graders under `judges/`. Run them via
`infra.langfuse.evals.datasets.runner.run_dataset_eval(... judge = ...)`."""
from __future__ import annotations
from . import datasets, judges


__all__ = ["datasets", "judges"]
