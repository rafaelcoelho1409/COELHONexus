# =============================================================================
# Elasticsearch Snapshot CronJob — every 6h, retention 20
# =============================================================================
# Reads ELASTIC_PASSWORD from the ECK-managed Secret at runtime.
# =============================================================================

apiVersion: batch/v1
kind: CronJob
metadata:
  name: elasticsearch-snapshot
  namespace: ${namespace}
  labels:
    app.kubernetes.io/name: elasticsearch-backup
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
            app.kubernetes.io/name: elasticsearch-backup
        spec:
          restartPolicy: OnFailure
          containers:
            - name: snapshot
              image: curlimages/curl:8.5.0
              envFrom:
                - secretRef:
                    name: ${creds_secret}
              env:
                - name: ELASTIC_PASSWORD
                  valueFrom:
                    secretKeyRef:
                      name: elasticsearch-es-elastic-user
                      key: elastic
              command:
                - /bin/sh
                - -c
                - |
                  set -euo pipefail
                  ES="https://$ELASTICSEARCH_HOST:9200"
                  TS=$(date +%Y%m%d-%H%M%S)
                  SNAP="snapshot-$${TS}"
                  RETENTION=${backup_retention}

                  echo "=== Creating snapshot $${SNAP} ==="
                  curl -sfku "elastic:$ELASTIC_PASSWORD" -X PUT \
                    "$ES/_snapshot/$SNAPSHOT_REPO/$${SNAP}?wait_for_completion=true" \
                    -H 'Content-Type: application/json' \
                    -d '{"indices":"*","ignore_unavailable":true,"include_global_state":true}'
                  echo

                  echo "=== Pruning to last $${RETENTION} ==="
                  ALL=$(curl -sfku "elastic:$ELASTIC_PASSWORD" \
                    "$ES/_snapshot/$SNAPSHOT_REPO/_all" \
                    | grep -oE '"snapshot":"[^"]+"' \
                    | cut -d'"' -f4 \
                    | sort)
                  TOTAL=$(echo "$ALL" | wc -l)
                  TO_DELETE=$$((TOTAL - RETENTION))
                  if [ "$TO_DELETE" -gt 0 ]; then
                    echo "$ALL" | head -n "$TO_DELETE" | while read -r snap; do
                      [ -z "$snap" ] && continue
                      echo "  delete: $snap"
                      curl -sfku "elastic:$ELASTIC_PASSWORD" -X DELETE \
                        "$ES/_snapshot/$SNAPSHOT_REPO/$snap" > /dev/null
                    done
                  else
                    echo "  nothing to prune (have $TOTAL, keep $RETENTION)"
                  fi

                  echo "=== Done ==="
              resources:
                requests:
                  cpu: 50m
                  memory: 64Mi
                limits:
                  memory: 128Mi
