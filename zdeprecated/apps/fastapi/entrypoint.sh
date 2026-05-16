#!/bin/bash

set -e

# Ensure logs directory exists
mkdir -p /app/logs

# Use --reload only in local/development (Skaffold), not production (ArgoCD).
# --reload-delay 2.0 debounces multi-file edit bursts (e.g. Claude Code
# editing 5+ files in <1s). Default is 0.25s which fires uvicorn reload
# multiple times mid-sync; 2s lets the whole burst land before reloading.
if [ "$ENVIRONMENT" = "local" ] || [ "$ENVIRONMENT" = "development" ]; then
  echo "Starting uvicorn with --reload (development mode, --reload-delay 2.0)"
  exec uvicorn app:app --host 0.0.0.0 --port 8000 \
    --reload \
    --reload-dir /app \
    --reload-delay 2.0 \
    --reload-exclude '.venv' \
    --reload-exclude '__pycache__' \
    --reload-exclude 'logs'
else
  echo "Starting uvicorn (production mode)"
  exec uvicorn app:app --host 0.0.0.0 --port 8000
fi
