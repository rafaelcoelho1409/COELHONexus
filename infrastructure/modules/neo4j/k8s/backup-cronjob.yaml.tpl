# =============================================================================
# Neo4j Backup CronJob
# =============================================================================
# Two-phase Pod:
#   - Init container (neo4j:5-community with cypher-shell): runs apoc.export.cypher.all
#     to /backup, falls back to basic Cypher export if APOC fails.
#   - Main container (amazon/aws-cli): uploads the gzipped Cypher script to
#     MinIO under `backups/neo4j/<timestamp>.cypher.gz` with retention.
#     (minio/mc is dead upstream — archived Jul 2026, Docker Hub pulls
#     denied. Ported from COELHO Cloud, 2026-09-28.)
#
# Restore: download the file, gunzip, pipe into cypher-shell.
# =============================================================================

apiVersion: batch/v1
kind: CronJob
metadata:
  name: ${release_name}-backup
  namespace: ${namespace}
  labels:
    app.kubernetes.io/name: neo4j-backup
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
            app.kubernetes.io/name: neo4j-backup
            app.kubernetes.io/instance: ${release_name}
        spec:
          restartPolicy: OnFailure
          volumes:
            - name: backup-data
              emptyDir: {}
          initContainers:
            - name: dump
              image: neo4j:5-community
              command:
                - /bin/bash
                - -c
                - |
                  set -euo pipefail

                  TIMESTAMP=$(date +%Y%m%d-%H%M%S)
                  BACKUP_FILE="neo4j-$${TIMESTAMP}.cypher.gz"
                  BACKUP_PATH="/backup/$${BACKUP_FILE}"
                  NEO4J_HOST="${release_name}"

                  echo "=== Neo4j Backup Started ==="

                  echo "Waiting for Neo4j..."
                  for i in $(seq 1 30); do
                    if cypher-shell -a bolt://$${NEO4J_HOST}:7687 -u neo4j -p "$${NEO4J_PASSWORD}" "RETURN 1" > /dev/null 2>&1; then
                      echo "Neo4j is available"
                      break
                    fi
                    sleep 5
                  done

                  CYPHER_FILE="/tmp/neo4j-export-$${TIMESTAMP}.cypher"

                  echo "Stats:"
                  cypher-shell -a bolt://$${NEO4J_HOST}:7687 -u neo4j -p "$${NEO4J_PASSWORD}" \
                    "CALL apoc.meta.stats() YIELD nodeCount, relCount RETURN nodeCount, relCount" 2>/dev/null || \
                    cypher-shell -a bolt://$${NEO4J_HOST}:7687 -u neo4j -p "$${NEO4J_PASSWORD}" \
                    "MATCH (n) RETURN count(n) as nodeCount"

                  echo "Exporting via apoc.export.cypher.all..."
                  if cypher-shell -a bolt://$${NEO4J_HOST}:7687 -u neo4j -p "$${NEO4J_PASSWORD}" \
                    "CALL apoc.export.cypher.all(null, {stream:true}) YIELD cypherStatements RETURN cypherStatements" > "$${CYPHER_FILE}" 2>/dev/null; then
                    echo "APOC export OK"
                  else
                    echo "APOC unavailable, basic export..."
                    {
                      echo "// Neo4j Backup - $${TIMESTAMP}"
                      cypher-shell -a bolt://$${NEO4J_HOST}:7687 -u neo4j -p "$${NEO4J_PASSWORD}" --format plain \
                        "MATCH (n) RETURN labels(n) as labels, properties(n) as props"
                      cypher-shell -a bolt://$${NEO4J_HOST}:7687 -u neo4j -p "$${NEO4J_PASSWORD}" --format plain \
                        "MATCH (a)-[r]->(b) RETURN labels(a), id(a), type(r), properties(r), labels(b), id(b)"
                    } > "$${CYPHER_FILE}"
                  fi

                  gzip -c "$${CYPHER_FILE}" > "$${BACKUP_PATH}"
                  echo "Size: $(du -h "$${BACKUP_PATH}" | cut -f1)"

                  echo "$${BACKUP_FILE}" > /backup/FILENAME
                  echo "OK" > /backup/STATUS
                  rm -f "$${CYPHER_FILE}"
              envFrom:
                - secretRef:
                    name: ${creds_secret}
              volumeMounts:
                - name: backup-data
                  mountPath: /backup
              resources:
                requests:
                  cpu: 100m
                  memory: 256Mi
                limits:
                  memory: 512Mi
          containers:
            - name: upload
              image: amazon/aws-cli:2.37.1
              command:
                - /bin/sh
                - -c
                - |
                  set -e

                  STATUS=$(cat /backup/STATUS 2>/dev/null || echo "MISSING")
                  if [ "$STATUS" != "OK" ]; then
                    echo "ERROR: No valid status from dump container"
                    exit 1
                  fi

                  FILENAME=$(cat /backup/FILENAME)
                  RETENTION=${backup_retention}
                  PREFIX="neo4j"

                  export AWS_ACCESS_KEY_ID="$MINIO_ACCESS_KEY" AWS_SECRET_ACCESS_KEY="$MINIO_SECRET_KEY" AWS_DEFAULT_REGION=us-east-1 AWS_EC2_METADATA_DISABLED=true
                  aws configure set s3.addressing_style path
                  EP="--endpoint-url $MINIO_ENDPOINT"

                  echo "=== Uploading $FILENAME ==="
                  if aws $EP s3api head-bucket --bucket "$MINIO_BUCKET" 2>/dev/null; then
                    echo "exists: $MINIO_BUCKET"
                  else
                    aws $EP s3api create-bucket --bucket "$MINIO_BUCKET"
                  fi
                  aws $EP s3 cp "/backup/$FILENAME" "s3://$MINIO_BUCKET/$PREFIX/"

                  echo "Pruning to last $RETENTION..."
                  KEYS=$(aws $EP s3api list-objects-v2 --bucket "$MINIO_BUCKET" --prefix "$PREFIX/" --query "sort_by(Contents, &LastModified)[].Key" --output text 2>/dev/null || echo "None")
                  if [ -n "$KEYS" ] && [ "$KEYS" != "None" ]; then
                    TOTAL=$(echo "$KEYS" | wc -w)
                    if [ "$TOTAL" -gt "$RETENTION" ]; then
                      echo "$KEYS" | tr '\t' '\n' | head -n $((TOTAL - RETENTION)) | while read -r old; do
                        if [ -n "$old" ]; then
                          echo "  delete: $old"
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
