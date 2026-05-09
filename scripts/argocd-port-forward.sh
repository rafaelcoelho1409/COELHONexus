#!/usr/bin/env bash
# COELHONexus Production Port Forwards
# Survives terminal close, auto-reconnects on pod restart
# Usage: ./scripts/port-forward.sh
# Stop:  pkill -f "port-forward -n coelhonexus"

pkill -f "port-forward -n coelhonexus" 2>/dev/null
sleep 1

nohup bash -c 'while true; do kubectl port-forward -n coelhonexus svc/coelhonexus-fastapi 23000:8000; sleep 10; done' > /tmp/pf-fastapi.log 2>&1 &
nohup bash -c 'while true; do kubectl port-forward -n coelhonexus svc/coelhonexus-fasthtml 23001:3000; sleep 10; done' > /tmp/pf-fasthtml.log 2>&1 &

echo "Port forwards started:"
echo "  FastAPI:  http://localhost:23000"
echo "  FastHTML: http://localhost:23001"
echo "  Stop:    pkill -f 'port-forward -n coelhonexus'"
