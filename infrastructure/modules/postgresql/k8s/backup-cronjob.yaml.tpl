# =============================================================================
# PostgreSQL backup CronJob — pg_dump → MinIO
# =============================================================================
# Pattern (kept from v1, simplified):
#   1. initContainer (postgres image): pg_dump → gzipped file in shared emptyDir
#   2. main container (minio/mc): mc cp the dump to MinIO; rotate old backups
#
# Variables interpolated: ${namespace}, ${release_name}, ${admin_user},
#   ${default_database}, ${backup_schedule}, ${backup_retention}
#
# Image tags pinned for reproducibility:
#   - postgres:18-bookworm  (matches PostgreSQL 18 from chart appVersion)
#   - minio/mc:latest       (MinIO upstream — they version per-release tag too if needed)
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
            # Phase 2: upload + rotate
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
                  mc mb --ignore-existing "minio/$MINIO_BUCKET/postgresql"
                  mc cp "/backup/$FILENAME" "minio/$MINIO_BUCKET/postgresql/"

                  if mc stat "minio/$MINIO_BUCKET/postgresql/$FILENAME" >/dev/null 2>&1; then
                    echo "Upload verified"
                  else
                    echo "ERROR: upload verify failed"
                    exit 1
                  fi

                  echo "=== rotate (keep last $RETENTION) ==="
                  mc ls "minio/$MINIO_BUCKET/postgresql/" \
                    | sort -t' ' -k1,1 -k2,2 \
                    | head -n -$RETENTION \
                    | while read -r line; do
                        old_backup=$(echo "$line" | rev | cut -d' ' -f1 | rev)
                        if [ -n "$old_backup" ]; then
                          echo "Deleting: $old_backup"
                          mc rm "minio/$MINIO_BUCKET/postgresql/$old_backup"
                        fi
                      done
                  echo "=== Done ==="
                  mc ls "minio/$MINIO_BUCKET/postgresql/" | tail -5
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
