"""Version markers for Book Distiller's agentic ingestion artifacts."""

MANIFEST_SCHEMA_VERSION = "1.4"
# This marks checkpoint compatibility. Keep it stable for node-code fixes so failed nodes can
# resume with deployed code; bump it only when saved state or graph topology cannot continue.
WORKFLOW_VERSION = "1.16"
CONTENT_CLASSIFIER_VERSION = "1.5"
CONTENTS_RECONCILER_VERSION = "4.0"
LEGACY_CONTENT_SELECTION_VERSION = "legacy-conservative-v1"
