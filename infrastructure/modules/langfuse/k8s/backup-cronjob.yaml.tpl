# =============================================================================
# CronJob — daily pg_dump of langfuse DB → MinIO backups/langfuse/postgres/
# =============================================================================
# Postgres' module backup CronJob only dumps the `default_database` (e.g.
# `postgres`), so each app DB needs its own pg_dump CronJob. Match v1's pattern.
#
# ClickHouse + MinIO blobs are NOT backed up here — too heavy for homelab.
# Restore-from-pg_dump recreates auth, projects, prompts, and metadata.
# Trace events live in MinIO already (durable on the same disk).
#
# The CronJob runs as one container (postgres:16-alpine ships psql + bash).
# `mc` is downloaded inside the container at runtime — avoids an init container.
# =============================================================================
apiVersion: batch/v1
kind: CronJob
metadata:
  name: ${release_name}-backup
  namespace: ${namespace}
  labels:
    app.kubernetes.io/name: langfuse
    app.kubernetes.io/component: backup
    app.kubernetes.io/managed-by: terraform
spec:
  schedule: "${schedule}"
  concurrencyPolicy: Forbid
  successfulJobsHistoryLimit: 2
  failedJobsHistoryLimit: 3
  jobTemplate:
    spec:
      ttlSecondsAfterFinished: 3600
      backoffLimit: 3
      template:
        metadata:
          labels:
            app.kubernetes.io/name: langfuse-backup
        spec:
          restartPolicy: OnFailure
          containers:
            - name: pgdump
              image: postgres:16-alpine
              env:
                - name: PGHOST
                  value: "${postgres_host}"
                - name: PGPORT
                  value: "${postgres_port}"
                - name: PGUSER
                  value: "${postgres_user}"
                - name: PGDATABASE
                  value: "${postgres_database}"
                - name: BUCKET
                  value: "${bucket}"
                - name: PREFIX
                  value: "${prefix}"
                - name: RETENTION_DAYS
                  value: "${retention_days}"
              envFrom:
                - secretRef:
                    name: ${pg_secret_name}
                - secretRef:
                    name: ${minio_secret_name}
              command: ["/bin/sh", "-c"]
              args:
                - |
                  set -euo pipefail
                  apk add --no-cache curl ca-certificates >/dev/null
                  curl -sSL https://dl.min.io/client/mc/release/linux-amd64/mc -o /usr/local/bin/mc
                  chmod +x /usr/local/bin/mc

                  STAMP=$(date -u +%Y-%m-%dT%H-%M-%SZ)
                  DUMP=/tmp/langfuse-$${STAMP}.sql.gz

                  echo "[1/3] pg_dump $${PGDATABASE}@$${PGHOST}:$${PGPORT}"
                  pg_dump --format=custom --no-owner --no-privileges --compress=9 \
                    --file=/tmp/langfuse.dump
                  gzip -c /tmp/langfuse.dump > "$${DUMP}"
                  ls -lh "$${DUMP}"

                  echo "[2/3] upload to s3://$${BUCKET}/$${PREFIX}/postgres/"
                  mc alias set m "$$MINIO_ENDPOINT" "$$MINIO_ACCESS_KEY" "$$MINIO_SECRET_KEY"
                  mc cp "$${DUMP}" "m/$${BUCKET}/$${PREFIX}/postgres/langfuse-$${STAMP}.sql.gz"

                  echo "[3/3] prune snapshots older than $$RETENTION_DAYS days"
                  mc rm --recursive --force --older-than "$${RETENTION_DAYS}d" \
                    "m/$${BUCKET}/$${PREFIX}/postgres/" || true

                  echo "Backup complete: $${STAMP}"
              resources:
                requests:
                  cpu: "50m"
                  memory: "128Mi"
                limits:
                  memory: "512Mi"
