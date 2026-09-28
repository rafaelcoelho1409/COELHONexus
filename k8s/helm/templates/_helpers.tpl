{{/*
Generate image name
Usage: {{ include "coelhonexus.imageName" (dict "appName" "fastapi" "root" .) }}
Images are specified with full registry path in values.yaml
*/}}
{{- define "coelhonexus.imageName" -}}
{{- index .root.Values .appName "image" -}}
{{- end -}}


{{/*
Common environment variables for all services (non-sensitive)
Credentials are loaded from secret via secretRef
*/}}
{{- define "coelhonexus.commonEnvVars" -}}
ENVIRONMENT: "{{ .Values.environment }}"
FASTAPI_HOST: "coelhonexus-fastapi"
LLM_ENDPOINT_URL: "{{ .Values.llm.endpoint.url }}"
LLM_API_KEY: "{{ .Values.llm.endpoint.apiKey }}"
LLM_MODEL: "{{ .Values.llm.endpoint.model }}"
REDIS_HOST: "{{ .Values.redis.host }}"
REDIS_PORT: "{{ .Values.redis.port }}"
MINIO_HOST: "{{ .Values.minio.host }}"
MINIO_PORT: "{{ .Values.minio.port }}"
MINIO_ENDPOINT: "{{ .Values.minio.endpoint }}"
MINIO_BUCKET_COELHONEXUS: "{{ .Values.minio.bucket }}"
POSTGRES_HOST: "{{ .Values.postgresql.host }}"
POSTGRES_PORT: "{{ .Values.postgresql.port }}"
POSTGRES_USER: "{{ .Values.postgresql.user }}"
POSTGRES_DATABASE: "{{ .Values.postgresql.database }}"
NEO4J_URI: "{{ .Values.neo4j.uri }}"
QDRANT_URL: "{{ .Values.qdrant.url }}"
QDRANT_PORT: "{{ .Values.qdrant.port }}"
ELASTICSEARCH_HOST: "{{ .Values.elasticsearch.host }}"
ELASTICSEARCH_USERNAME: "{{ .Values.elasticsearch.username }}"
# Playwright CDP endpoints (browser automation, bypasses IP blocking)
PLAYWRIGHT_CDP_HEADLESS: "{{ .Values.playwright.cdp_headless }}"
PLAYWRIGHT_CDP_HEADED: "{{ .Values.playwright.cdp_headed }}"
# Embedding model (NVIDIA NIM API — see docs/NVIDIA-NIM-EMBEDDING-MODELS.md)
NVIDIA_EMBEDDING_MODEL: "{{ .Values.embedding.model }}"
# OpenTelemetry (2026-05-12 night) — dual-export to Alloy (LGTM) + LangFuse v3.
# Set OTEL_EXPORTER_OTLP_ENDPOINT to Alloy's OTLP gRPC receiver (typically
# alloy.monitoring.svc.cluster.local:4317). Set LANGFUSE_OTLP_ENDPOINT to the
# LangFuse v3 OTLP endpoint pattern (`<host>/api/public/otel`).
# Leave OTEL_EXPORTER_OTLP_ENDPOINT empty in dev to disable the pipeline entirely.
OTEL_EXPORTER_OTLP_ENDPOINT: "{{ .Values.otel.alloy_endpoint }}"
OTEL_SERVICE_NAME: "{{ .Values.otel.service_name }}"
OTEL_SERVICE_VERSION: "{{ .Values.otel.service_version }}"
OTEL_RESOURCE_ATTRIBUTES: "deployment.environment={{ .Values.environment }},service.namespace=coelhonexus"
LANGFUSE_OTLP_ENDPOINT: "{{ .Values.otel.langfuse_otlp_endpoint }}"
# Opt into the latest GenAI semantic-convention attributes (`gen_ai.*`) on
# every LLM span. Marked "Development" upstream; pin here and bump on each
# OTel release. Lets LangFuse render input/output messages, token usage,
# tool calls, finish reasons natively (no Langfuse-specific extras needed).
OTEL_SEMCONV_STABILITY_OPT_IN: "gen_ai_latest_experimental"
# LiteLLM v2 OTel — one trace per LLM request following the gen_ai semconv
# above. Replaces the legacy per-deployment span shape. Fail-soft: legacy
# emission still works if the flag is unset, but we want the gen_ai shape
# in both Tempo and LangFuse.
LITELLM_OTEL_V2: "true"
# Deploy-identity attrs propagated to every span/metric via the OTel
# Resource (build_resource in infra/otel/exporters.py). `GIT_SHA` lets
# Grafana annotations diff "which deploy regressed?"; the chart version
# carries forward image-tag context. Both are optional — when unset the
# resource builder skips them.
GIT_SHA: {{ .Values.otel.git_sha | default "" | quote }}
HELM_CHART_VERSION: {{ .Chart.Version | quote }}
# chapter_propose Optimal-Stopping feature flag. Diverges from the code's own
# default ("false") — kept live here rather than in values.yaml/params.py so
# rolling it back doesn't need a rebuild.
DD_PROPOSE_OPTIMAL_STOPPING: "true"
# Per-study chapter concurrency semaphore. Roll back to "1" if 429 cascades
# resume. Kept live here (not values.yaml/params.py) as a fast rollback lever.
DD_STUDY_SEM: "2"
# "1" = abort right after a degraded chapter_propose on a big corpus (opt-in,
# currently off). Kept live here as an incident-response toggle.
DD_PLANNER_ABORT_ON_DEGRADE: "0"
{{- end -}}


{{/*
ConfigMap settings
*/}}
{{- define "coelhonexus.ConfigMapSettings" -}}
kind: ConfigMap
metadata:
  name: coelhonexus-{{ .appName }}-configmap
  namespace: {{ .root.Release.Namespace }}
{{- end -}}


{{/*
Deployment settings
*/}}
{{- define "coelhonexus.DeploymentSettings" -}}
kind: Deployment
metadata:
  name: coelhonexus-{{ .appName }}
  namespace: {{ .root.Release.Namespace }}
  labels:
    app.kubernetes.io/name: {{ .root.Chart.Name }}
    app.kubernetes.io/instance: {{ .root.Release.Name }}
    app.kubernetes.io/version: {{ .root.Chart.AppVersion }}
    app.kubernetes.io/component: {{ .appName }}
    app.kubernetes.io/managed-by: {{ .root.Release.Service }}
{{- end -}}


{{/*
Service settings
*/}}
{{- define "coelhonexus.ServiceSettings" -}}
kind: Service
metadata:
  name: coelhonexus-{{ .appName }}
  namespace: {{ .root.Release.Namespace }}
  labels:
    app: coelhonexus-{{ .appName }}
spec:
  selector:
    app: coelhonexus-{{ .appName }}
{{- end -}}


{{/*
PVC settings
*/}}
{{- define "coelhonexus.PVCSettings" -}}
kind: PersistentVolumeClaim
metadata:
  name: coelhonexus-{{ .appName }}-pvc
  namespace: {{ .root.Release.Namespace }}
spec:
  accessModes:
    - ReadWriteOnce
  resources:
    requests:
      storage: {{ index .root.Values .appName "storageSize" }}
  storageClassName: {{ index .root.Values .appName "storageClassName" }}
{{- end -}}


{{/*
Deployment spec settings
*/}}
{{- define "coelhonexus.DeploymentSpecSettings" -}}
selector:
  matchLabels:
    app: coelhonexus-{{ .appName }}
template:
  metadata:
    labels:
      app: coelhonexus-{{ .appName }}
  spec:
    {{- if and (eq .root.Values.environment "production") (.root.Values.registry.imagePullSecret) }}
    imagePullSecrets:
      - name: {{ .root.Values.registry.imagePullSecret }}
    {{- end }}
    #securityContext:
    #  runAsNonRoot: true
    #  runAsUser: 1000
    #  fsGroup: 1000
    containers:
      - name: coelhonexus-{{ .appName }}
        image: {{ include "coelhonexus.imageName" (dict "appName" .appName "root" .root) }}
        imagePullPolicy: {{ index .root.Values .appName "imagePullPolicy" }}
        #securityContext:
        #  allowPrivilegeEscalation: false
        #  capabilities:
        #    drop:
        #      - ALL
        #  readOnlyRootFilesystem: false
        envFrom:
          - configMapRef:
              name: coelhonexus-{{ .appName }}-configmap
        env:
          {{- include "coelhonexus.secretEnvVars" .root | nindent 10 }}
          {{- if .root.Values.llmCredentials.manageKek }}
          - name: KD_CREDS_KEY
            valueFrom:
              secretKeyRef:
                name: {{ .root.Values.llmCredentials.kekSecretName }}
                key: {{ .root.Values.llmCredentials.kekSecretKey }}
                optional: true
          {{- end }}
          {{- if .root.Values.llmCredentials.importEnvKeys }}
          - name: KD_CREDS_IMPORT_ENV
            value: "1"
          {{- end }}
{{- end -}}


{{/*
Secret environment variables - maps secret keys to env var names
Iterates over secretMappings defined in values.yaml
*/}}
{{- define "coelhonexus.secretEnvVars" -}}
{{- range .Values.secretMappings }}
- name: {{ .envName }}
  valueFrom:
    secretKeyRef:
      name: {{ $.Values.secretName }}
      key: {{ .key }}
      optional: true
{{- end }}
{{- end -}}


{{- define "coelhonexus.DeploymentResources" -}}
resources:
  requests:
    memory: {{ index .root.Values .appName "resources" "requests" "memory" }}
    cpu: {{ index .root.Values .appName "resources" "requests" "cpu" }}
  limits:
    memory: {{ index .root.Values .appName "resources" "limits" "memory" }}
    cpu: {{ index .root.Values .appName "resources" "limits" "cpu" }}
{{- end -}}


{{/*
Generate fullname for resources
*/}}
{{- define "coelhonexus.fullname" -}}
{{- .Release.Name | trunc 63 | trimSuffix "-" }}
{{- end }}


{{/*
Common labels
*/}}
{{- define "coelhonexus.labels" -}}
helm.sh/chart: {{ .Chart.Name }}-{{ .Chart.Version | replace "+" "_" }}
{{ include "coelhonexus.selectorLabels" . }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end }}


{{/*
Selector labels
*/}}
{{- define "coelhonexus.selectorLabels" -}}
app.kubernetes.io/name: {{ .Chart.Name }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end }}


{{/*
Service ports settings - ClusterIP for local (Skaffold), full portsSettings for production (ArgoCD)
Usage: {{ include "coelhonexus.ServicePortsSettings" (dict "appName" "fastapi" "root" .) }}
*/}}
{{- define "coelhonexus.ServicePortsSettings" -}}
{{- if eq .root.Values.environment "local" }}
  type: ClusterIP
  ports:
    {{- range (index .root.Values .appName "portsSettings" "ports") }}
    - name: {{ .name }}
      port: {{ .port }}
      targetPort: {{ .targetPort }}
      protocol: {{ .protocol }}
    {{- end }}
{{- else }}
  {{- toYaml (index .root.Values .appName "portsSettings") | nindent 2 }}
{{- end }}
{{- end -}}


{{/*
Probe settings - renders all probes (startup, liveness, readiness) for a container
Usage: {{ include "coelhonexus.ProbeSettings" (dict "appName" "fastapi" "root" .) }}

Probe execution order:
1. startupProbe  - Runs ONLY during startup, disables liveness/readiness until success
2. livenessProbe - Runs after startup succeeds, restarts pod on failure
3. readinessProbe - Runs after startup succeeds, removes from Service on failure
*/}}
{{- define "coelhonexus.ProbeSettings" -}}
{{- $appConfig := index .root.Values .appName -}}
{{- if $appConfig.startupProbeSettings }}
{{ toYaml $appConfig.startupProbeSettings }}
{{- end }}
{{- if $appConfig.livenessProbeSettings }}
{{ toYaml $appConfig.livenessProbeSettings }}
{{- end }}
{{- if $appConfig.readinessProbeSettings }}
{{ toYaml $appConfig.readinessProbeSettings }}
{{- end }}
{{- end -}}
