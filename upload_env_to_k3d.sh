#!/bin/bash

# Simple script to convert .env file to Kubernetes secret
# Usage: ./upload_env_to_k3d.sh [env-file] [namespace] [secret-name]

# Default values
ENV_FILE="${1:-.env}"
NAMESPACE="${2:-coelhonexus}"
SECRET_NAME="${3:-coelhonexus-secret}"

echo "Converting $ENV_FILE to Kubernetes secret..."
echo "Namespace: $NAMESPACE"
echo "Secret name: $SECRET_NAME"
echo ""

# Check if .env file exists
if [ ! -f "$ENV_FILE" ]; then
    echo "File '$ENV_FILE' not found!"
    echo "Create a .env file with your variables:"
    echo "   AWS_ACCESS_KEY_ID=your-key"
    echo "   OPENAI_API_KEY=your-key"
    echo "   etc..."
    exit 1
fi

# Create namespace if it doesn't exist
kubectl create namespace $NAMESPACE --dry-run=client -o yaml | kubectl apply -f - >/dev/null 2>&1

# Delete existing secret if it exists
kubectl delete secret $SECRET_NAME -n $NAMESPACE >/dev/null 2>&1

# Create a temporary file for the secret YAML
SECRET_YAML=$(mktemp)

# Start building the secret YAML
cat > "$SECRET_YAML" << EOF
apiVersion: v1
kind: Secret
metadata:
  name: $SECRET_NAME
  namespace: $NAMESPACE
type: Opaque
data:
EOF

echo "Processing environment variables..."

# Process each line in .env file
while IFS= read -r line || [ -n "$line" ]; do
    # Skip empty lines and comments
    if [[ -z "$line" || "$line" =~ ^[[:space:]]*# ]]; then
        continue
    fi
    
    # Skip GitLab-specific variables
    if [[ "$line" =~ ^GITLAB_ || "$line" =~ ^PROJECT_ID ]]; then
        echo "Skipping GitLab variable: ${line%%=*}"
        continue
    fi
    
    # Skip if no equals sign
    if [[ ! "$line" =~ = ]]; then
        continue
    fi
    
    # Extract key and value
    key="${line%%=*}"
    value="${line#*=}"
    
    # Clean up whitespace
    key=$(echo "$key" | xargs)
    value=$(echo "$value" | xargs)
    
    # Remove quotes if present
    if [[ "$value" =~ ^\".*\"$ || "$value" =~ ^\'.*\'$ ]]; then
        value="${value:1:-1}"
    fi
    
    # Convert environment variable name to the GitLab CI format
    # Transform: EVAGPT_AGENT_ID -> evagpt-agent-id
    secret_key=$(echo "$key" | tr '[:upper:]' '[:lower:]' | tr '_' '-')
    
    # Clean value of any null bytes or problematic characters
    clean_value=$(echo "$value" | tr -d '\0' | tr -d '\r')
    
    # Check if value appears to be base64 encoded (and should be decoded)
    # Only decode if it's a long string that looks like base64
    if [[ ${#clean_value} -gt 20 ]] && echo "$clean_value" | base64 -d >/dev/null 2>&1; then
        # Try to decode and check if result looks like plain text
        if decoded_value=$(echo "$clean_value" | base64 -d 2>/dev/null) && [[ -n "$decoded_value" ]]; then
            # Check if decoded value contains only printable characters (likely was base64)
            if echo "$decoded_value" | grep -q '^[[:print:][:space:]]*$'; then
                final_value="$decoded_value"
                echo "Decoded base64 value for $secret_key"
            else
                # Decoded value contains non-printable chars, use original
                final_value="$clean_value"
            fi
        else
            final_value="$clean_value"
        fi
    else
        # Value doesn't look like base64, use as-is
        final_value="$clean_value"
    fi
    
    # Base64 encode the final value for Kubernetes secret
    encoded_value=$(echo -n "$final_value" | base64 -w 0)
    
    # Add to YAML file
    echo "  $secret_key: $encoded_value" >> "$SECRET_YAML"
    
    # Show what we're adding (truncate long values)
    display_value="${final_value:0:20}"
    if [ ${#final_value} -gt 20 ]; then
        display_value="${display_value}..."
    fi
    echo "$secret_key: $display_value"
    
done < "$ENV_FILE"

# Apply the secret YAML
echo ""
echo "Creating secret..."
if kubectl apply -f "$SECRET_YAML" >/dev/null 2>&1; then
    echo "Secret '$SECRET_NAME' created successfully!"
    
    # Show summary
    key_count=$(kubectl get secret $SECRET_NAME -n $NAMESPACE -o jsonpath='{.data}' | jq -r 'keys | length' 2>/dev/null || echo "?")
    echo "Secret contains $key_count environment variables"
    echo ""
    echo "Your secret is ready for use in:"
    echo "   - Namespace: $NAMESPACE"
    echo "   - Secret name: $SECRET_NAME"
    echo ""
    echo "Verify with: kubectl get secret $SECRET_NAME -n $NAMESPACE -o yaml"
    echo "Start development with: skaffold dev"
else
    echo "Failed to create secret. Debug with:"
    echo "kubectl apply -f $SECRET_YAML"
    exit 1
fi

# Clean up
rm -f "$SECRET_YAML"