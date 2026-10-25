{{- define "factory.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" -}}
{{- end -}}

{{- define "factory.fullname" -}}
{{- if .Values.fullnameOverride -}}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" -}}
{{- else -}}
{{- $name := default .Chart.Name .Values.nameOverride -}}
{{- if contains $name .Release.Name -}}
{{- .Release.Name | trunc 63 | trimSuffix "-" -}}
{{- else -}}
{{- printf "%s-%s" .Release.Name $name | trunc 63 | trimSuffix "-" -}}
{{- end -}}
{{- end -}}
{{- end -}}

{{- define "factory.labels" -}}
app.kubernetes.io/name: {{ include "factory.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
app.kubernetes.io/part-of: damf-factory
helm.sh/chart: {{ printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" }}
{{- end -}}

{{- define "factory.selectorLabels" -}}
app.kubernetes.io/name: {{ include "factory.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end -}}

{{- define "factory.image" -}}
{{- printf "%s:%s" .Values.image.repository (.Values.image.tag | default .Chart.AppVersion) -}}
{{- end -}}

{{- define "factory.env" -}}
- name: FACTORY_WORKFLOW_ENGINE
  value: {{ .Values.workflowEngine | quote }}
- name: FACTORY_PROVIDER_MODE
  value: {{ .Values.providerMode | quote }}
- name: FACTORY_FAKE_ENVIRONMENTS
  value: {{ .Values.fakeEnvironments | quote }}
- name: FACTORY_MIGRATIONS_MODE
  value: {{ .Values.migrationsMode | quote }}
- name: FACTORY_TEMPORAL_TARGET
  value: {{ .Values.temporal.target | quote }}
- name: FACTORY_TEMPORAL_NAMESPACE
  value: {{ .Values.temporal.namespace | quote }}
- name: FACTORY_TEMPORAL_TASK_QUEUE
  value: {{ .Values.temporal.taskQueue | quote }}
- name: FACTORY_TEMPORAL_RETENTION_HOURS
  value: {{ .Values.temporal.retentionHours | quote }}
- name: FACTORY_SSE_CHANNEL
  value: {{ .Values.sse.channel | quote }}
- name: FACTORY_SSE_POLL_SECONDS
  value: {{ .Values.sse.pollSeconds | quote }}
- name: FACTORY_SSE_HEARTBEAT_SECONDS
  value: {{ .Values.sse.heartbeatSeconds | quote }}
- name: FACTORY_DEFAULT_IDEMPOTENCY_RETENTION_HOURS
  value: {{ .Values.idempotency.retentionHours | quote }}
- name: FACTORY_NOTIFICATION_MAX_ATTEMPTS
  value: {{ .Values.notifications.maxAttempts | quote }}
- name: FACTORY_WEBHOOK_TIMEOUT_SECONDS
  value: {{ .Values.notifications.webhookTimeoutSeconds | quote }}
- name: FACTORY_DEFAULT_MAX_UPLOAD_BYTES
  value: {{ .Values.uploads.maxBytes | quote }}
- name: SECRET_BACKEND
  value: {{ .Values.secrets.backend | quote }}
- name: FACTORY_SMTP_SECRET_REF
  value: {{ .Values.secrets.smtpSecretRef | quote }}
- name: FACTORY_WEBHOOK_SECRET_REF
  value: {{ .Values.secrets.webhookSecretRef | quote }}
- name: DATABASE_URL
  valueFrom:
    secretKeyRef:
      name: {{ .Values.database.existingSecret }}
      key: {{ .Values.database.secretKey }}
- name: OBJECT_STORE_URL
  valueFrom:
    secretKeyRef:
      name: {{ .Values.objectStore.existingSecret }}
      key: {{ .Values.objectStore.secretKey }}
{{- end -}}
