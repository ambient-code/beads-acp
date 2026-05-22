{{/*
Chart name, truncated to 63 characters.
*/}}
{{- define "beads-acp.name" -}}
{{- default .Chart.Name .Values.nameOverride | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Fully qualified app name. Uses release name + chart name, truncated to 63 characters.
If release name already contains the chart name, just use release name.
*/}}
{{- define "beads-acp.fullname" -}}
{{- if .Values.fullnameOverride }}
{{- .Values.fullnameOverride | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- $name := default .Chart.Name .Values.nameOverride }}
{{- if contains $name .Release.Name }}
{{- .Release.Name | trunc 63 | trimSuffix "-" }}
{{- else }}
{{- printf "%s-%s" .Release.Name $name | trunc 63 | trimSuffix "-" }}
{{- end }}
{{- end }}
{{- end }}

{{/*
Chart name and version for the chart label.
*/}}
{{- define "beads-acp.chart" -}}
{{- printf "%s-%s" .Chart.Name .Chart.Version | replace "+" "_" | trunc 63 | trimSuffix "-" }}
{{- end }}

{{/*
Standard labels applied to all resources.
*/}}
{{- define "beads-acp.labels" -}}
helm.sh/chart: {{ include "beads-acp.chart" . }}
{{ include "beads-acp.selectorLabels" . }}
{{- if .Chart.AppVersion }}
app.kubernetes.io/version: {{ .Chart.AppVersion | quote }}
{{- end }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end }}

{{/*
Selector labels used in matchLabels and pod templates.
*/}}
{{- define "beads-acp.selectorLabels" -}}
app.kubernetes.io/name: {{ include "beads-acp.name" . }}
app.kubernetes.io/instance: {{ .Release.Name }}
{{- end }}

{{/*
Component-specific labels. Adds app.kubernetes.io/component to standard labels.
Usage: {{ include "beads-acp.componentLabels" (dict "component" "dolt" "context" .) }}
*/}}
{{- define "beads-acp.componentLabels" -}}
{{ include "beads-acp.labels" .context }}
app.kubernetes.io/component: {{ .component }}
{{- end }}

{{/*
Component-specific selector labels.
Usage: {{ include "beads-acp.componentSelectorLabels" (dict "component" "dolt" "context" .) }}
*/}}
{{- define "beads-acp.componentSelectorLabels" -}}
{{ include "beads-acp.selectorLabels" .context }}
app.kubernetes.io/component: {{ .component }}
{{- end }}

{{/*
ServiceAccount name for a given component.
Usage: {{ include "beads-acp.serviceAccountName" (dict "component" "dolt" "context" .) }}
*/}}
{{- define "beads-acp.serviceAccountName" -}}
{{- printf "%s-%s" (include "beads-acp.fullname" .context) .component }}
{{- end }}
