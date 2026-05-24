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
REDIS_HOST: "{{ .Values.redis.host }}"
REDIS_PORT: "{{ .Values.redis.port }}"
MINIO_HOST: "{{ .Values.minio.host }}"
MINIO_PORT: "{{ .Values.minio.port }}"
MINIO_ENDPOINT: "{{ .Values.minio.endpoint }}"
POSTGRES_HOST: "{{ .Values.postgresql.host }}"
POSTGRES_PORT: "{{ .Values.postgresql.port }}"
POSTGRES_USER: "{{ .Values.postgresql.user }}"
POSTGRES_DATABASE: "{{ .Values.postgresql.database }}"
NEO4J_URI: "{{ .Values.neo4j.uri }}"
QDRANT_URL: "{{ .Values.qdrant.url }}"
QDRANT_PORT: "{{ .Values.qdrant.port }}"
ELASTICSEARCH_HOST: "{{ .Values.elasticsearch.host }}"
ELASTICSEARCH_USERNAME: "{{ .Values.elasticsearch.username }}"
# Proxy configuration (WARP host/port, Tor host/port)
WARP_PROXY_HOST: "{{ .Values.proxy.warp.host }}"
WARP_PROXY_PORT: "{{ .Values.proxy.warp.port }}"
TOR_PROXY_HOST: "{{ .Values.proxy.tor.host }}"
TOR_PROXY_PORT: "{{ .Values.proxy.tor.port }}"
# Playwright CDP endpoints (browser automation, bypasses IP blocking)
PLAYWRIGHT_CDP_HEADLESS: "{{ .Values.playwright.cdp_headless }}"
PLAYWRIGHT_CDP_HEADED: "{{ .Values.playwright.cdp_headed }}"
# Embedding model (NVIDIA NIM API — see docs/NVIDIA-NIM-EMBEDDING-MODELS.md)
NVIDIA_EMBEDDING_MODEL: "{{ .Values.embedding.model }}"
# SearXNG metasearch (in-cluster) — used by Knowledge Distiller docs-URL resolver
SEARXNG_URL: "{{ .Values.searxng.url }}"
# KD planner MAP step routing — "1" enables the classical pipeline (rotator
# embed + community_detection + KeyLLM); "0" keeps the legacy LLM path.
# Read at runtime by graphs/knowledge/distiller.py.
DD_USE_CLASSICAL_MAP: "{{ .Values.dd.useClassicalMap }}"
# Phase 1.3 (2026-05-13): routes synth grader through classical scorer when "1".
# See kd.useClassicalGrader in values.yaml.
DD_USE_CLASSICAL_GRADER: "{{ .Values.dd.useClassicalGrader }}"
# Phase 2.1 (2026-05-13): replaces critic's per-chapter LLM faithfulness call
# with dd-embed similarity heuristic when "1". See kd.useClassicalCritic above.
DD_USE_CLASSICAL_CRITIC: "{{ .Values.dd.useClassicalCritic }}"
# Phase 3.1 (2026-05-13): routes Phase A outline through classical path when "1".
# See kd.useClassicalOutline in values.yaml.
DD_USE_CLASSICAL_OUTLINE: "{{ .Values.dd.useClassicalOutline }}"
# Phase 4 (2026-05-13): when "1", Self-Refine loop attempts deterministic patches
# on patchable grader Issue dims before LLM re-synth, and uses template-based
# adjustment text instead of the ADJUSTMENT_PROMPT LLM call. See
# kd.useClassicalRefiner in values.yaml.
DD_USE_CLASSICAL_REFINER: "{{ .Values.dd.useClassicalRefiner }}"
# Phase 5 (2026-05-13): when "1", per-chapter curator pass runs through
# services/knowledge/curator_classical.py (deterministic regex passes, no LLM
# call). See kd.useClassicalCurator in values.yaml.
DD_USE_CLASSICAL_CURATOR: "{{ .Values.dd.useClassicalCurator }}"
# Phase 5 (2026-05-13): when "1", summary.md / assembler step runs through
# services/knowledge/summary_classical.py (deterministic reading plan +
# small-LLM creative artifacts only). See kd.useClassicalSummary in values.yaml.
DD_USE_CLASSICAL_SUMMARY: "{{ .Values.dd.useClassicalSummary }}"
# Scope B (2026-05-12 night): when "1", section synth uses the dd-synth
# non-reasoning pool instead of dd-all. See kd.useSynthPool in values.yaml.
DD_USE_SYNTH_POOL: "{{ .Values.dd.useSynthPool }}"
# Fix #2 (2026-05-12 night): per-chapter model pinning. See kd.pinChapterModel
# in values.yaml.
DD_PIN_CHAPTER_MODEL: "{{ .Values.dd.pinChapterModel }}"
# OpenTelemetry (2026-05-12 night) — dual-export to Alloy (LGTM) + LangFuse v3.
# Set OTEL_EXPORTER_OTLP_ENDPOINT to Alloy's OTLP gRPC receiver (typically
# alloy.monitoring.svc.cluster.local:4317). Set LANGFUSE_OTLP_ENDPOINT to the
# LangFuse v3 OTLP endpoint pattern (`<host>/api/public/otel`).
# Leave OTEL_EXPORTER_OTLP_ENDPOINT empty in dev to disable the pipeline entirely.
OTEL_EXPORTER_OTLP_ENDPOINT: "{{ .Values.otel.alloy_endpoint }}"
OTEL_SERVICE_NAME: "{{ .Values.otel.service_name }}"
OTEL_SERVICE_VERSION: "{{ .Values.otel.service_version }}"
OTEL_RESOURCE_ATTRIBUTES: "deployment.environment={{ .Values.environment }},service.namespace=knowledge-distiller"
LANGFUSE_OTLP_ENDPOINT: "{{ .Values.otel.langfuse_otlp_endpoint }}"
# Scope B (2026-05-12 night): per-process LLM concurrency cap. See
# kd.llmGlobalConcurrency in values.yaml and _get_llm_semaphore() in
# graphs/knowledge/helpers.py.
DD_LLM_GLOBAL_CONCURRENCY: "{{ .Values.dd.llmGlobalConcurrency }}"
# R8 (2026-05-11): when "1", MAP runs ONE global pass (label_corpus_classical)
# instead of per-shard. See `kd.globalMap` in values.yaml and `_use_global_map`
# in graphs/knowledge/distiller.py.
DD_GLOBAL_MAP: "{{ .Values.dd.globalMap }}"
# Phase 1 rotator rebuild (2026-05-14): when "1", build the LiteLLM Router's
# model_list from live discovery (services/discovery.py) + benchmark ranking
# (services/benchmarks.py) instead of the hand-curated static catalog in
# services/llm_chain.py. Falls back to static catalog on any error.
# See kd.dynamicCatalog in values.yaml.
DD_DYNAMIC_CATALOG: "{{ .Values.dd.dynamicCatalog }}"
# Phase 2 — ParetoBandit ALWAYS-ON adaptive routing (2026-05-14): the bandit
# runs by default; set "1" to disable (emergency rollback to Phase 1).
# See dd.paretoBanditDisable in values.yaml + docs/KD-ROTATOR-ALWAYS-ON-BANDIT-MAY2026.md.
DD_PARETO_BANDIT_DISABLE: "{{ .Values.dd.paretoBanditDisable }}"
# Phase D (2026-05-23): deterministic soft-membership boundary resolver for refine.
# See dd.refineUseGmm in values.yaml + docs/KD-PLANNER-SOTA-IMPROVEMENTS-2026-05-23.md.
KD_REFINE_USE_GMM: "{{ .Values.dd.refineUseGmm }}"
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
