# =============================================================================
# Neo4j Backup CronJob
# =============================================================================
# Two-phase Pod:
#   - Init container (neo4j:5-community with cypher-shell): runs apoc.export.cypher.all
#     to /backup, falls back to basic Cypher export if APOC fails.
#   - Main container (minio/mc): uploads the gzipped Cypher script to MinIO
#     under `backups/neo4j/<timestamp>.cypher.gz` with retention.
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
              image: minio/mc:latest
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

                  echo "=== Uploading $FILENAME ==="
                  mc alias set m "$MINIO_ENDPOINT" "$MINIO_ACCESS_KEY" "$MINIO_SECRET_KEY"
                  mc cp "/backup/$FILENAME" "m/$MINIO_BUCKET/neo4j/"

                  echo "Pruning to last $RETENTION..."
                  mc ls "m/$MINIO_BUCKET/neo4j/" | \
                    sort -t' ' -k1,1 -k2,2 | \
                    head -n -$RETENTION | \
                    while read -r line; do
                      old=$(echo "$line" | rev | cut -d' ' -f1 | rev)
                      if [ -n "$old" ]; then
                        echo "  delete: $old"
                        mc rm "m/$MINIO_BUCKET/neo4j/$old"
                      fi
                    done

                  echo "=== Done ==="
                  mc ls "m/$MINIO_BUCKET/neo4j/" | tail -5
              envFrom:
                - secretRef:
                    name: ${creds_secret}
              volumeMounts:
                - name: backup-data
                  mountPath: /backup
              resources:
                requests:
                  cpu: 50m
                  memory: 64Mi
                limits:
                  memory: 128Mi
