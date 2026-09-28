# =============================================================================
# Redis backup CronJob — BGSAVE + RDB → MinIO
# =============================================================================
# Pattern (kept from v1):
#   1. initContainer (redis): BGSAVE → poll until done → redis-cli --rdb dump
#      to shared emptyDir
#   2. main container (aws-cli): upload RDB to MinIO; rotate old backups
#      (minio/mc is dead upstream — archived Jul 2026, Docker Hub pulls
#      denied. Ported from COELHO Cloud, 2026-09-28.)
#
# Variables interpolated:
#   ${namespace}, ${release_name}, ${backup_schedule}, ${backup_retention}
#
# Auth: REDISCLI_AUTH env var (auto-used by redis-cli) comes from the Secret
#   `${release_name}-minio-backup` created in main.tf.
# =============================================================================
apiVersion: batch/v1
kind: CronJob
metadata:
  name: ${release_name}-backup
  namespace: ${namespace}
  labels:
    app.kubernetes.io/name: redis-backup
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
            app.kubernetes.io/name: redis-backup
            app.kubernetes.io/instance: ${release_name}
        spec:
          restartPolicy: OnFailure
          volumes:
            - name: backup-data
              emptyDir: {}
          initContainers:
            - name: dump
              image: redis:8-bookworm
              command:
                - /bin/bash
                - -c
                - |
                  set -euo pipefail
                  TIMESTAMP=$(date +%Y%m%d-%H%M%S)
                  BACKUP_FILE="redis-$${TIMESTAMP}.rdb"
                  BACKUP_PATH="/backup/$${BACKUP_FILE}"
                  REDIS_HOST="${release_name}-master"

                  echo "=== Redis BGSAVE start (host=$${REDIS_HOST}) ==="
                  redis-cli -h $${REDIS_HOST} BGSAVE
                  while true; do
                    in_progress=$(redis-cli -h $${REDIS_HOST} INFO persistence | grep rdb_bgsave_in_progress | cut -d: -f2 | tr -d '\r')
                    [ "$${in_progress}" = "0" ] && break
                    echo "  in progress..."
                    sleep 2
                  done
                  echo "BGSAVE completed"

                  redis-cli -h $${REDIS_HOST} --rdb "$${BACKUP_PATH}"
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
                  cpu: 50m
                  memory: 64Mi
                limits:
                  memory: 128Mi
          containers:
            - name: upload
              # minio/mc is dead upstream (archived Jul 2026) — aws-cli below.
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
                  PREFIX="redis"

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
