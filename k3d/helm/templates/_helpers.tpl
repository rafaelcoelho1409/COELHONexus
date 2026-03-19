{{/*
Generate image name based on environment
Usage: {{ include "coelhonexus.imageName" (dict "appName" "fastapi" "root" .) }}
*/}}
{{- define "coelhonexus.imageName" -}}
{{- $image := index .root.Values .appName "image" -}}
{{- if eq .root.Values.environment "production" -}}
  {{- if not (hasPrefix .root.Values.registry.url $image) -}}
    {{- printf "%s/%s" .root.Values.registry.url $image -}}
  {{- else -}}
    {{- $image -}}
  {{- end -}}
{{- else -}}
  {{- $image -}}
{{- end -}}
{{- end -}}


{{/*
Common environment variables for all services (non-sensitive)
Credentials are loaded from secret via secretRef
*/}}
{{- define "coelhonexus.commonEnvVars" -}}
FASTAPI_HOST: "coelhonexus-fastapi"
REDIS_URL: "redis://{{ .Values.redis.host }}:{{ .Values.redis.port }}"
MINIO_HOST: "{{ .Values.minio.host }}"
MINIO_PORT: "{{ .Values.minio.port }}"
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
      - name: coelhonexus-{{ .appName }}-container
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
          - secretRef:
              name: {{ .root.Values.secretName }}
              optional: true
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
