# =============================================================================
# Qdrant Backup CronJob — snapshot every collection → MinIO
# =============================================================================
# Two-phase Pod:
#   - Init container (alpine + curl/jq): POST /collections/{c}/snapshots for
#     every collection, downloads each snapshot to /backup, then DELETEs the
#     snapshot from Qdrant (cleanup).
#   - Main container (amazon/aws-cli): uploads each snapshot to MinIO under
#     `backups/qdrant/<collection>/<timestamp>.snapshot`. Retention applied
#     per-collection (keep last N).
#     (minio/mc is dead upstream — archived Jul 2026, Docker Hub pulls
#     denied. Ported from COELHO Cloud, 2026-09-28.)
#
# Inherits MinIO creds + Qdrant API key from the backup_creds Secret.
# =============================================================================

apiVersion: batch/v1
kind: CronJob
metadata:
  name: ${release_name}-backup
  namespace: ${namespace}
  labels:
    app.kubernetes.io/name: qdrant-backup
    app.kubernetes.io/instance: ${release_name}
    app.kubernetes.io/managed-by: terraform
spec:
  schedule: "${backup_schedule}"
  concurrencyPolicy: Forbid
  successfulJobsHistoryLimit: 3
  failedJobsHistoryLimit: 3
  jobTemplate:
    spec:
      ttlSecondsAfterFinished: 86400
      template:
        metadata:
          labels:
            app.kubernetes.io/name: qdrant-backup
            app.kubernetes.io/instance: ${release_name}
        spec:
          restartPolicy: OnFailure
          volumes:
            - name: backup-data
              emptyDir: {}
          initContainers:
            # Phase 1 — create + download snapshots from Qdrant
            - name: snapshot
              image: alpine:3.19
              command:
                - /bin/sh
                - -c
                - |
                  set -euo pipefail

                  echo "Installing dependencies..."
                  apk add --no-cache curl jq > /dev/null

                  TIMESTAMP=$(date +%Y%m%d-%H%M%S)
                  QDRANT_URL="${qdrant_url}"

                  echo "=== Qdrant Backup Started ==="
                  echo "Timestamp: $${TIMESTAMP}"
                  echo "Qdrant URL: $${QDRANT_URL}"

                  qdrant_curl() {
                    if [ -n "$${QDRANT_API_KEY:-}" ]; then
                      curl -sf -H "api-key: $${QDRANT_API_KEY}" "$@"
                    else
                      curl -sf "$@"
                    fi
                  }

                  echo "Fetching collections..."
                  COLLECTIONS=$(qdrant_curl "$${QDRANT_URL}/collections" | jq -r '.result.collections[].name')

                  if [ -z "$${COLLECTIONS}" ]; then
                    echo "No collections found. Nothing to backup."
                    echo "SKIP" > /backup/STATUS
                    exit 0
                  fi

                  echo "Found collections: $${COLLECTIONS}"

                  for COLLECTION in $${COLLECTIONS}; do
                    echo "--- Backing up collection: $${COLLECTION} ---"

                    SNAPSHOT_RESPONSE=$(qdrant_curl -X POST "$${QDRANT_URL}/collections/$${COLLECTION}/snapshots")
                    SNAPSHOT_NAME=$(echo "$${SNAPSHOT_RESPONSE}" | jq -r '.result.name')

                    if [ -z "$${SNAPSHOT_NAME}" ] || [ "$${SNAPSHOT_NAME}" = "null" ]; then
                      echo "ERROR: Failed to create snapshot for $${COLLECTION}"
                      echo "Response: $${SNAPSHOT_RESPONSE}"
                      continue
                    fi

                    echo "Snapshot: $${SNAPSHOT_NAME}"

                    SNAPSHOT_FILE="/backup/$${COLLECTION}-$${TIMESTAMP}.snapshot"
                    qdrant_curl "$${QDRANT_URL}/collections/$${COLLECTION}/snapshots/$${SNAPSHOT_NAME}" -o "$${SNAPSHOT_FILE}"
                    echo "Downloaded: $(du -h "$${SNAPSHOT_FILE}" | cut -f1)"

                    qdrant_curl -X DELETE "$${QDRANT_URL}/collections/$${COLLECTION}/snapshots/$${SNAPSHOT_NAME}" > /dev/null

                    echo "$${COLLECTION}|$${COLLECTION}-$${TIMESTAMP}.snapshot" >> /backup/FILES
                  done

                  echo "OK" > /backup/STATUS
                  echo "Snapshots created successfully"
              envFrom:
                - secretRef:
                    name: ${creds_secret}
              volumeMounts:
                - name: backup-data
                  mountPath: /backup
              resources:
                requests:
                  cpu: 100m
                  memory: 128Mi
                limits:
                  memory: 512Mi
          containers:
            # Phase 2 — upload to MinIO with per-collection retention (aws-cli)
            - name: upload
              image: amazon/aws-cli:2.37.1
              command:
                - /bin/sh
                - -c
                - |
                  set -e

                  STATUS=$(cat /backup/STATUS 2>/dev/null || echo "MISSING")
                  if [ "$STATUS" = "SKIP" ]; then
                    echo "No collections to backup, skipping upload"
                    exit 0
                  fi
                  if [ "$STATUS" != "OK" ]; then
                    echo "ERROR: No valid status from snapshot container"
                    exit 1
                  fi

                  RETENTION=${backup_retention}

                  export AWS_ACCESS_KEY_ID="$MINIO_ACCESS_KEY" AWS_SECRET_ACCESS_KEY="$MINIO_SECRET_KEY" AWS_DEFAULT_REGION=us-east-1 AWS_EC2_METADATA_DISABLED=true
                  aws configure set s3.addressing_style path
                  EP="--endpoint-url $MINIO_ENDPOINT"

                  echo "=== Uploading to MinIO ==="
                  if aws $EP s3api head-bucket --bucket "$MINIO_BUCKET" 2>/dev/null; then
                    echo "exists: $MINIO_BUCKET"
                  else
                    aws $EP s3api create-bucket --bucket "$MINIO_BUCKET"
                  fi

                  while IFS='|' read -r collection filename; do
                    echo "--- $collection ---"
                    aws $EP s3 cp "/backup/$filename" "s3://$MINIO_BUCKET/qdrant/$collection/"

                    # Retention: keep last N snapshots per collection
                    echo "Pruning to last $RETENTION snapshots..."
                    KEYS=$(aws $EP s3api list-objects-v2 --bucket "$MINIO_BUCKET" --prefix "qdrant/$collection/" --query "sort_by(Contents, &LastModified)[].Key" --output text 2>/dev/null || echo "None")
                    if [ -n "$KEYS" ] && [ "$KEYS" != "None" ]; then
                      TOTAL=$(echo "$KEYS" | wc -w)
                      if [ "$TOTAL" -gt "$RETENTION" ]; then
                        echo "$KEYS" | tr '\t' '\n' | head -n $((TOTAL - RETENTION)) | while read -r old; do
                          if [ -n "$old" ]; then
                            echo "  delete: $old"
                            aws $EP s3api delete-object --bucket "$MINIO_BUCKET" --key "$old" >/dev/null
                          fi
                        done
                      fi
                    fi
                  done < /backup/FILES

                  echo "=== Backup complete ==="
                  aws $EP s3 ls --recursive "s3://$MINIO_BUCKET/qdrant/" | tail -10
              envFrom:
                - secretRef:
                    name: ${creds_secret}
              volumeMounts:
                - name: backup-data
                  mountPath: /backup
              # aws-cli (Python) is heavier than mc — 256Mi limit headroom.
              resources:
                requests:
                  cpu: 50m
                  memory: 64Mi
                limits:
                  memory: 256Mi
