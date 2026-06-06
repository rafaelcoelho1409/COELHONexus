"""infra/elasticsearch — env reads + index name constants + connection tunables.

Env vars are injected from the Helm chart's `commonEnvVars` template
(`k8s/helm/templates/_helpers.tpl`) — `ELASTICSEARCH_HOST` is the full
URL (scheme + host + port), `ELASTICSEARCH_USERNAME` defaults to
`elastic`, `ELASTICSEARCH_PASSWORD` is loaded from `coelhonexus-secret`.

The deprecated index names are kept verbatim so re-ingesting from an
existing cluster is a no-op."""
from __future__ import annotations

import os


# Connection — `ELASTICSEARCH_HOST` is a full URL with scheme + port
# (the deprecated value points at the ECK-operator HTTPS service with a
# self-signed cert; `verify_certs=False` accepts it).
ES_HOST = os.environ.get("ELASTICSEARCH_HOST", "https://elasticsearch-es-http.elasticsearch.svc.cluster.local:9200")
ES_USERNAME = os.environ.get("ELASTICSEARCH_USERNAME", "elastic")
ES_PASSWORD = os.environ.get("ELASTICSEARCH_PASSWORD", "") or None

# Self-signed ECK cert in dev → skip verification. Deprecated did the
# same (`app.py:L110`).
ES_VERIFY_CERTS = os.environ.get("ELASTICSEARCH_VERIFY_CERTS", "false").lower() in ("1", "true", "yes")

# Async client request timeout.
TIMEOUT_S = 30.0


# Deprecated index names — kept verbatim (`helpers.py:L1958, L1977`).
INDEX_METADATA = "coelhonexus-youtube-metadata"
INDEX_TRANSCRIPTIONS = "coelhonexus-youtube-transcriptions"
