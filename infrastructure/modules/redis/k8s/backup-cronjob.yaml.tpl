# =============================================================================
# Redis backup CronJob — BGSAVE + RDB → MinIO
# =============================================================================
# Pattern (kept from v1):
#   1. initContainer (redis): BGSAVE → poll until done → redis-cli --rdb dump
#      to shared emptyDir
#   2. main container (mc): upload RDB to MinIO; rotate old backups
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
              image: minio/mc:latest
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

                  echo "=== mc upload to MinIO ==="
                  mc alias set minio "$MINIO_ENDPOINT" "$MINIO_ACCESS_KEY" "$MINIO_SECRET_KEY"
                  mc mb --ignore-existing "minio/$MINIO_BUCKET/redis"
                  mc cp "/backup/$FILENAME" "minio/$MINIO_BUCKET/redis/"

                  if mc stat "minio/$MINIO_BUCKET/redis/$FILENAME" >/dev/null 2>&1; then
                    echo "Upload verified"
                  else
                    echo "ERROR: upload verify failed"
                    exit 1
                  fi

                  echo "=== rotate (keep last $RETENTION) ==="
                  mc ls "minio/$MINIO_BUCKET/redis/" \
                    | sort -t' ' -k1,1 -k2,2 \
                    | head -n -$RETENTION \
                    | while read -r line; do
                        old=$(echo "$line" | rev | cut -d' ' -f1 | rev)
                        if [ -n "$old" ]; then
                          echo "Deleting: $old"
                          mc rm "minio/$MINIO_BUCKET/redis/$old"
                        fi
                      done
                  echo "=== Done ==="
                  mc ls "minio/$MINIO_BUCKET/redis/" | tail -5
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
