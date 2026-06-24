"""One-shot dataset uploader — push a `k8s/helm/files/langfuse/<feature>/<name>/`
directory into LangFuse as a versioned dataset. Idempotent at the dataset
level (re-runs only add new items; LangFuse dedupes by source identity
when provided).

Usage (Python):
    from infra.langfuse.datasets import upload_dataset_from_fixtures
    upload_dataset_from_fixtures(
        "/etc/langfuse-fixtures/dd/reference_book",
        dataset_name = "dd.reference_book.v1",
        description  = "DD planner chapter-outline gold corpus",
    )

Usage (CLI, run inside the FastAPI image):
    python -m infra.langfuse.datasets.uploader \\
        /etc/langfuse-fixtures/dd/reference_book dd.reference_book.v1

Returns the number of items uploaded (0 if LangFuse is unavailable).
"""
from __future__ import annotations

import json
import logging
import sys
from pathlib import Path

from ..client import get_client


logger = logging.getLogger(__name__)


def upload_dataset_from_fixtures(
    fixture_dir:  str | Path,
    *,
    dataset_name: str,
    description:  str = "",
) -> int:
    """Push `inputs.json` from `fixture_dir` into LangFuse. Returns count."""
    client = get_client()
    if client is None:
        logger.warning("[langfuse-datasets] client unavailable — upload skipped")
        return 0
    inputs_file = Path(fixture_dir) / "inputs.json"
    if not inputs_file.exists():
        logger.warning(f"[langfuse-datasets] {inputs_file} not found")
        return 0
    items = json.loads(inputs_file.read_text())
    try:
        client.create_dataset(name = dataset_name, description = description)
    except Exception as e:
        # Already exists is the common case — log and continue.
        logger.debug(
            f"[langfuse-datasets] create_dataset (likely exists): "
            f"{type(e).__name__}: {e}"
        )
    n = 0
    for item in items:
        try:
            client.create_dataset_item(
                dataset_name    = dataset_name,
                input           = item.get("input"),
                expected_output = item.get("expected_output"),
                metadata        = item.get("metadata") or {},
            )
            n += 1
        except Exception as e:
            logger.warning(
                f"[langfuse-datasets] create_dataset_item failed: "
                f"{type(e).__name__}: {e}"
            )
    logger.info(
        f"[langfuse-datasets] uploaded {n}/{len(items)} items → {dataset_name!r}"
    )
    return n


def _main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(
            "usage: python -m infra.langfuse.datasets.uploader "
            "<fixture_dir> <dataset_name> [description]",
            file = sys.stderr,
        )
        return 2
    fixture_dir = argv[0]
    dataset_name = argv[1]
    description = argv[2] if len(argv) > 2 else ""
    n = upload_dataset_from_fixtures(
        fixture_dir,
        dataset_name = dataset_name,
        description  = description,
    )
    return 0 if n > 0 else 1


if __name__ == "__main__":
    raise SystemExit(_main(sys.argv[1:]))
