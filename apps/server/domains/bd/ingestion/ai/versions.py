"""Version markers for Book Distiller's agentic ingestion artifacts."""

MANIFEST_SCHEMA_VERSION = "1.5"
# This marks checkpoint compatibility. Keep it stable for node-code fixes so failed nodes can
# resume with deployed code; bump it only when saved state or graph topology cannot continue.
WORKFLOW_VERSION = "1.18"
CONTENTS_RECONCILER_VERSION = "4.0"
VISUAL_ENRICHMENT_VERSION = "1.3"
VISUAL_ENRICHMENT_COMPATIBLE_VERSIONS = ("1.2", "1.1", "1.0")
