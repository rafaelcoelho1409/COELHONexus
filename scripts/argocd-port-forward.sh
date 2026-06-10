#!/usr/bin/env bash
# COELHONexus Production Port Forwards
# Survives terminal close, auto-reconnects on pod restart
# Usage: ./scripts/port-forward.sh
# Stop:  pkill -f "port-forward -n coelhonexus"

pkill -f "port-forward -n coelhonexus" 2>/dev/null
sleep 1

# Prod ports mirror Skaffold's dev ports (2302X) in the 2300X range — same
# trailing digit per service: fastapi 23020→23000, flower 23022→23002,
# fasthtml 23023→23003.
nohup bash -c 'while true; do kubectl port-forward -n coelhonexus svc/coelhonexus-fastapi 23000:8000; sleep 10; done' > /tmp/pf-fastapi.log 2>&1 &
nohup bash -c 'while true; do kubectl port-forward -n coelhonexus svc/coelhonexus-flower 23002:5555; sleep 10; done' > /tmp/pf-flower.log 2>&1 &
nohup bash -c 'while true; do kubectl port-forward -n coelhonexus svc/coelhonexus-fasthtml 23003:3000; sleep 10; done' > /tmp/pf-fasthtml.log 2>&1 &

echo "Port forwards started:"
echo "  FastAPI:  http://localhost:23000"
echo "  Flower:   http://localhost:23002"
echo "  FastHTML: http://localhost:23003"
echo "  Stop:    pkill -f 'port-forward -n coelhonexus'"
