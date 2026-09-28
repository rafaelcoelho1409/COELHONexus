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
# S3 upload uses aws-cli from Alpine packages (minio/mc is dead upstream —
# archived Jul 2026, Docker Hub pulls denied, dl.min.io unreliable). Ported
# from COELHO Cloud 2026-09-28.
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
                  apk add --no-cache aws-cli >/dev/null
                  export AWS_ACCESS_KEY_ID="$MINIO_ACCESS_KEY" AWS_SECRET_ACCESS_KEY="$MINIO_SECRET_KEY" AWS_DEFAULT_REGION=us-east-1 AWS_EC2_METADATA_DISABLED=true
                  aws configure set s3.addressing_style path
                  EP="--endpoint-url $MINIO_ENDPOINT"

                  STAMP=$(date -u +%Y-%m-%dT%H-%M-%SZ)
                  DUMP=/tmp/langfuse-$${STAMP}.sql.gz

                  echo "[1/3] pg_dump $${PGDATABASE}@$${PGHOST}:$${PGPORT}"
                  pg_dump --format=custom --no-owner --no-privileges --compress=9 \
                    --file=/tmp/langfuse.dump
                  gzip -c /tmp/langfuse.dump > "$${DUMP}"
                  ls -lh "$${DUMP}"

                  echo "[2/3] upload to s3://$${BUCKET}/$${PREFIX}/postgres/"
                  aws $EP s3 cp "$${DUMP}" "s3://$${BUCKET}/$${PREFIX}/postgres/langfuse-$${STAMP}.sql.gz"

                  echo "[3/3] prune snapshots older than $RETENTION_DAYS days"
                  CUTOFF=$(date -u -d "$RETENTION_DAYS days ago" +%Y-%m-%dT%H:%M:%S.000Z)
                  OLD=$(aws $EP s3api list-objects-v2 --bucket "$BUCKET" --prefix "$PREFIX/postgres/" --query "Contents[?LastModified<='$CUTOFF'].Key" --output text 2>/dev/null || echo "None")
                  if [ -n "$OLD" ] && [ "$OLD" != "None" ]; then
                    echo "$OLD" | tr '\t' '\n' | while read -r key; do
                      if [ -n "$key" ]; then
                        echo "  delete: $key"
                        aws $EP s3api delete-object --bucket "$BUCKET" --key "$key" >/dev/null
                      fi
                    done
                  else
                    echo "Nothing older than $CUTOFF"
                  fi

                  echo "Backup complete: $${STAMP}"
              resources:
                requests:
                  cpu: "50m"
                  memory: "128Mi"
                limits:
                  memory: "512Mi"
