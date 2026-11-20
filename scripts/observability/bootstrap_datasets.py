"""Bootstrap LangFuse datasets — upload every fixture mounted at `/etc/langfuse-fixtures/`
to LangFuse as a versioned dataset.

This uploads the YCS QA fixtures mounted at
`/etc/langfuse-fixtures/ycs/qa_pairs` to LangFuse as a versioned dataset.

Run inside the server container (the only place `langfuse` Python SDK +
`langfuse.<host>` env vars are reliably available):

    kubectl exec -i -n coelhonexus-dev <server-pod> -c coelhonexus-server -- \
        bash -c "PYTHONPATH=/app python /app/scripts/observability/bootstrap_datasets.py"

Idempotent: re-runs only add NEW items to existing datasets (LangFuse
dedupes by source identity when provided). Safe to run after fixture edits.

If LangFuse is unreachable / no creds, each upload logs a warning and
returns 0 — never raises. The main app pipeline is not affected; this is
a one-shot ops script.
"""
from __future__ import annotations

import logging
import sys


logging.basicConfig(
    level  = logging.INFO,
    format = "%(levelname)s %(name)s: %(message)s",
)
logger = logging.getLogger("bootstrap_datasets")


# Mapping fixture_dir → (dataset_name, description).
DATASETS = [
    (
        "/etc/langfuse-fixtures/ycs/qa_pairs",
        "ycs.qa_pairs.v1",
        "YCS Ask-pipeline Q/A pairs — graded by the RAGAS-style relevance judge.",
    ),
]


def main() -> int:
    from infra.langfuse.datasets import upload_dataset_from_fixtures

    total_uploaded = 0
    n_datasets_succeeded = 0

    for fixture_dir, dataset_name, description in DATASETS:
        logger.info("→ uploading %s from %s", dataset_name, fixture_dir)
        try:
            n = upload_dataset_from_fixtures(
                fixture_dir,
                dataset_name = dataset_name,
                description  = description,
            )
        except Exception as exc:
            logger.warning("  ✗ %s: %s", dataset_name, exc)
            continue
        if n > 0:
            n_datasets_succeeded += 1
            total_uploaded += n
            logger.info("  ✓ %s: %d item(s) uploaded", dataset_name, n)
        else:
            logger.info("  · %s: 0 items uploaded (already present, or LangFuse unreachable)", dataset_name)

    logger.info(
        "done — %d/%d datasets succeeded, %d total items uploaded",
        n_datasets_succeeded, len(DATASETS), total_uploaded,
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
