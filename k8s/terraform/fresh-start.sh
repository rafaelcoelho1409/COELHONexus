#!/bin/bash
# Complete fresh start cleanup and deployment
# This script automates the two-stage deployment process required for fresh installations

echo "=========================================="
echo "Complete Fresh Start Cleanup"
echo "=========================================="
echo ""

# 1. Delete K3D cluster (if exists)
echo "Step 1: Deleting K3D cluster..."
k3d cluster delete coelhonexus 2>/dev/null || echo "Cluster already deleted"

# 2. Delete K3D registry (if exists)
echo ""
echo "Step 2: Deleting K3D registry..."
k3d registry delete coelhonexus-registry 2>/dev/null || echo "Registry already deleted"

# 3. Remove all Terraform state
echo ""
echo "Step 3: Removing Terraform state..."
terraform state rm $(terraform state list) 2>/dev/null || true
rm -f terraform.tfstate terraform.tfstate.backup
rm -f terraform.tfstate.*.backup
ls -lh terraform.tfstate* 2>/dev/null || echo "No state files found"

# 4. Clean Terraform generated files
echo ""
echo "Step 4: Cleaning Terraform generated files..."
rm -rf modules/k3d/.generated/

# 5. Clean Terraform cache
echo ""
echo "Step 5: Cleaning Terraform cache..."
rm -rf .terraform/
rm -f .terraform.lock.hcl

# 6. Clean kubeconfig contexts
echo ""
echo "Step 6: Cleaning kubeconfig..."
kubectl config delete-context k3d-coelhonexus 2>/dev/null || true
kubectl config delete-cluster k3d-coelhonexus 2>/dev/null || true
kubectl config unset users.admin@k3d-coelhonexus 2>/dev/null || true

# 7. Clean Docker networks
echo ""
echo "Step 7: Cleaning Docker networks..."
docker network rm k3d-coelhonexus 2>/dev/null || echo "Network already removed"

echo ""
echo "=========================================="
echo "Cleanup Complete!"
echo "=========================================="
echo ""

# 8. Deploy infrastructure with two-stage approach
echo "=========================================="
echo "Starting Two-Stage Deployment"
echo "=========================================="
echo ""
echo "This two-stage approach prevents 'connection refused' errors"
echo "by creating the cluster before Kubernetes resources."
echo ""

# Stage 1: Initialize Terraform
echo "Stage 1/3: Initializing Terraform..."
echo "Creating required directories..."
mkdir -p modules/k3d/.generated
terraform init

if [ $? -ne 0 ]; then
    echo "Error: Terraform init failed"
    exit 1
fi

echo ""
# Stage 2: Create K3D cluster first
echo "Stage 2/3: Creating K3D cluster..."
echo "This ensures the cluster exists before Kubernetes provider connects."
terraform apply -target=module.k3d_cluster -auto-approve

if [ $? -ne 0 ]; then
    echo "Error: K3D cluster creation failed"
    exit 1
fi

echo ""
# Stage 3: Deploy all services
echo "Stage 3/3: Deploying all services..."
echo "Now deploying Rancher"
terraform apply -auto-approve

if [ $? -ne 0 ]; then
    echo "Error: Service deployment failed"
    exit 1
fi

echo ""
echo "=========================================="
echo "Deployment Complete!"
echo "=========================================="
echo ""

# Verify registry mirrors configuration
echo "Verifying registry mirrors configuration..."
if docker exec k3d-coelhonexus-server-0 cat /etc/rancher/k3s/registries.yaml 2>/dev/null | grep -q "localhost:5000"; then
    echo "✓ Registry mirrors configured successfully"
else
    echo "✗ Warning: Registry mirrors may not be configured properly"
fi

echo ""
echo "Next steps:"
echo "  1. Test Skaffold deployment:"
echo "     cd .. && skaffold dev --profile=dev"
echo ""
echo "  2. Get service URLs:"
echo "     terraform output summary"
echo ""
echo "  3. Get credentials:"
echo "     terraform output argocd_admin_password_command"
echo "     terraform output gitlab_root_password_command"
echo ""
