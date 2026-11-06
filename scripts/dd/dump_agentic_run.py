"""Dump the stored artifacts of one Docs Distiller agentic ingestion run to JSON on stdout, for offline replay
(scripts/dd/replay_agentic_run.py). Read-only.

Run inside a server pod (it needs MinIO access), redirecting stdout locally:

    kubectl -n coelhonexus-dev exec -i deploy/coelhonexus-server -c coelhonexus-server -- \
        python - <slug> <run_id> < scripts/dd/dump_agentic_run.py > run.json

One object: {"manifest.json": ..., "pages/0000-<slug>.md": ..., "assessments/0000.json": ..., "audit/0000.json": ...}, each value the file text.
"""
import json
import sys

import infra

slug, run_id = sys.argv[1], sys.argv[2]
prefix = f"dd/{slug}/ingestion/agentic/{run_id}/"
client = infra.minio.service.new_sync_client()
bucket = infra.minio.service.bucket()
files = {}
for page in client.get_paginator("list_objects_v2").paginate(Bucket = bucket, Prefix = prefix):
    for item in page.get("Contents", []):
        key = item["Key"]
        if key.endswith("/") or key.startswith(prefix + "vault/"):
            continue
        files[key[len(prefix):]] = client.get_object(Bucket = bucket, Key = key)["Body"].read().decode("utf-8", "replace")
print(json.dumps(files))
