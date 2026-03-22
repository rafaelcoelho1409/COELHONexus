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
FASTAPI_HOST: "coelhonexus-fastapi"
REDIS_HOST: "{{ .Values.redis.host }}"
REDIS_PORT: "{{ .Values.redis.port }}"
MINIO_HOST: "{{ .Values.minio.host }}"
MINIO_PORT: "{{ .Values.minio.port }}"
MINIO_ENDPOINT: "{{ .Values.minio.endpoint }}"
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
