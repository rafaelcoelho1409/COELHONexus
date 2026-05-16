#!/bin/bash

set -e

# Use --reload only in local/development (Skaffold), not production (ArgoCD).
# --reload-delay 2.0 debounces multi-file edit bursts (Claude Code edits 5+
# files in <1s; default 0.25s would fire uvicorn reload multiple times
# mid-sync). Mirrors apps/fastapi/entrypoint.sh pattern.
if [ "$ENVIRONMENT" = "local" ] || [ "$ENVIRONMENT" = "development" ]; then
  echo "Starting uvicorn with --reload (development mode, --reload-delay 2.0)"
  exec uvicorn main:app --host 0.0.0.0 --port 3000 \
    --reload \
    --reload-dir /app \
    --reload-delay 2.0 \
    --reload-exclude '.venv' \
    --reload-exclude '__pycache__'
else
  echo "Starting uvicorn (production mode)"
  exec uvicorn main:app --host 0.0.0.0 --port 3000
fi
