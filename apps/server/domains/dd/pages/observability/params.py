from __future__ import annotations


# The metric instruments an ingestion run reports to, by the key they are registered under in `infra.opentelemetry` (entities.py).
INSTRUMENT_RUN_TOTAL = "ingestion_run_total"
INSTRUMENT_RUN_DURATION = "ingestion_run_duration"
INSTRUMENT_OUTPUT_FILES = "ingestion_output_files"
INSTRUMENT_OUTPUT_BYTES = "ingestion_output_bytes"

# The label of a run that has no tier or outcome to report.
UNKNOWN = "unknown"
