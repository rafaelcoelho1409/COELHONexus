#!/bin/bash

set -e

# Ensure logs directory exists
mkdir -p /app/logs

exec uvicorn app:app --host 0.0.0.0 --port 8000 --reload
