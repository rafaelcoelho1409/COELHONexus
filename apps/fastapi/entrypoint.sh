#!/bin/bash

set -e

# Ensure logs directory exists
mkdir -p /app/logs

# Use --reload only in local/development (Skaffold), not production (ArgoCD)
if [ "$ENVIRONMENT" = "local" ] || [ "$ENVIRONMENT" = "development" ]; then
  echo "Starting uvicorn with --reload (development mode)"
  exec uvicorn app:app --host 0.0.0.0 --port 8000 --reload --reload-dir /app
else
  echo "Starting uvicorn (production mode)"
  exec uvicorn app:app --host 0.0.0.0 --port 8000
fi
