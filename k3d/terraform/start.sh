#!/bin/bash
# Start/restart K3D cluster with all services

terraform init
terraform apply -target=module.k3d -auto-approve
terraform apply -auto-approve

# Verify registry mirrors
docker exec k3d-coelhonexus-server-0 cat /etc/rancher/k3s/registries.yaml 2>/dev/null | grep -q "localhost:5000" && echo "✓ Registry mirrors configured" || echo "✗ Registry mirrors not configured"

# Skaffold (when it doesn't install dependencies properly)
helm repo add community-charts https://community-charts.github.io/helm-charts
helm repo update
helm dependency build