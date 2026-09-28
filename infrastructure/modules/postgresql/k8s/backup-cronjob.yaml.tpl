# =============================================================================
# PostgreSQL backup CronJob — pg_dump → MinIO
# =============================================================================
# Pattern (kept from v1, simplified):
#   1. initContainer (postgres image): pg_dump → gzipped file in shared emptyDir
#   2. main container (aws-cli): s3 cp the dump to MinIO; rotate old backups
#
# Variables interpolated: ${namespace}, ${release_name}, ${admin_user},
#   ${default_database}, ${backup_schedule}, ${backup_retention}
#
# Image tags pinned for reproducibility:
#   - postgres:18-bookworm  (matches PostgreSQL 18 from chart appVersion)
#   - amazon/aws-cli:2.37.1 (minio/mc is dead upstream — archived Jul 2026,
#     Docker Hub pulls denied. Ported from COELHO Cloud, 2026-09-28.)
#
# MinIO credentials come from Secret postgresql-minio-backup (created by main.tf).
# =============================================================================
apiVersion: batch/v1
kind: CronJob
metadata:
  name: ${release_name}-backup
  namespace: ${namespace}
  labels:
    app.kubernetes.io/name: postgresql-backup
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
            app.kubernetes.io/name: postgresql-backup
            app.kubernetes.io/instance: ${release_name}
        spec:
          restartPolicy: OnFailure
          volumes:
            - name: backup-data
              emptyDir: {}
          initContainers:
            # Phase 1: dump
            - name: dump
              image: postgres:18-bookworm
              command:
                - /bin/bash
                - -c
                - |
                  set -euo pipefail
                  TIMESTAMP=$(date +%Y%m%d-%H%M%S)
                  BACKUP_FILE="postgresql-${default_database}-$${TIMESTAMP}.sql.gz"
                  BACKUP_PATH="/backup/$${BACKUP_FILE}"
                  echo "=== pg_dump start (db=${default_database}) ==="
                  pg_dump -h ${release_name} \
                          -U ${admin_user} \
                          -d ${default_database} \
                          --no-password \
                          --format=plain \
                          --clean \
                          --if-exists \
                    | gzip > "$${BACKUP_PATH}"
                  echo "Size: $(du -h "$${BACKUP_PATH}" | cut -f1)"
                  echo "$${BACKUP_FILE}" > /backup/FILENAME
                  echo "OK" > /backup/STATUS
              envFrom:
                - secretRef:
                    name: ${release_name}-minio-backup
              volumeMounts:
                - name: backup-data
                  mountPath: /backup
              resources:
                requests:
                  cpu: 100m
                  memory: 128Mi
                limits:
                  memory: 256Mi
          containers:
            # Phase 2: upload + rotate (minio/mc is dead upstream — aws-cli)
            - name: upload
              image: amazon/aws-cli:2.37.1
              command:
                - /bin/sh
                - -c
                - |
                  set -e
                  STATUS=$(cat /backup/STATUS 2>/dev/null || echo "MISSING")
                  if [ "$STATUS" != "OK" ]; then
                    echo "ERROR: dump init container did not complete"
                    exit 1
                  fi
                  FILENAME=$(cat /backup/FILENAME)
                  RETENTION=${backup_retention}
                  PREFIX="postgresql"

                  export AWS_ACCESS_KEY_ID="$MINIO_ACCESS_KEY" AWS_SECRET_ACCESS_KEY="$MINIO_SECRET_KEY" AWS_DEFAULT_REGION=us-east-1 AWS_EC2_METADATA_DISABLED=true
                  aws configure set s3.addressing_style path
                  EP="--endpoint-url $MINIO_ENDPOINT"

                  echo "=== aws-cli upload to MinIO ==="
                  if aws $EP s3api head-bucket --bucket "$MINIO_BUCKET" 2>/dev/null; then
                    echo "exists: $MINIO_BUCKET"
                  else
                    aws $EP s3api create-bucket --bucket "$MINIO_BUCKET"
                  fi
                  aws $EP s3 cp "/backup/$FILENAME" "s3://$MINIO_BUCKET/$PREFIX/"

                  if aws $EP s3api head-object --bucket "$MINIO_BUCKET" --key "$PREFIX/$FILENAME" >/dev/null 2>&1; then
                    echo "Upload verified"
                  else
                    echo "ERROR: upload verify failed"
                    exit 1
                  fi

                  echo "=== rotate (keep last $RETENTION) ==="
                  KEYS=$(aws $EP s3api list-objects-v2 --bucket "$MINIO_BUCKET" --prefix "$PREFIX/" --query "sort_by(Contents, &LastModified)[].Key" --output text 2>/dev/null || echo "None")
                  if [ -n "$KEYS" ] && [ "$KEYS" != "None" ]; then
                    TOTAL=$(echo "$KEYS" | wc -w)
                    if [ "$TOTAL" -gt "$RETENTION" ]; then
                      echo "$KEYS" | tr '\t' '\n' | head -n $((TOTAL - RETENTION)) | while read -r old; do
                        if [ -n "$old" ]; then
                          echo "Deleting: $old"
                          aws $EP s3api delete-object --bucket "$MINIO_BUCKET" --key "$old" >/dev/null
                        fi
                      done
                    else
                      echo "Nothing to prune ($TOTAL <= $RETENTION)"
                    fi
                  else
                    echo "No objects under $PREFIX/"
                  fi
                  echo "=== Done ==="
                  aws $EP s3 ls "s3://$MINIO_BUCKET/$PREFIX/" | tail -5
              envFrom:
                - secretRef:
                    name: ${release_name}-minio-backup
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
