{{/* Labels on every object, so `kubectl get all -l app.kubernetes.io/instance=elusion` finds them. */}}
{{- define "elusion.labels" -}}
app.kubernetes.io/part-of: elusion
app.kubernetes.io/instance: {{ .Release.Name }}
app.kubernetes.io/managed-by: {{ .Release.Service }}
{{- end }}

{{/* The two Secret names; rendering stops with a clear message if either is empty. */}}
{{- define "elusion.secret.postgres" -}}
{{- required "secrets.postgresPassword must name the Secret holding POSTGRES_PASSWORD (create it with kubectl first)" .Values.secrets.postgresPassword -}}
{{- end }}
{{- define "elusion.secret.openai" -}}
{{- required "secrets.openaiApiKey must name the Secret holding OPENAI_API_KEY (create it with kubectl first)" .Values.secrets.openaiApiKey -}}
{{- end }}

{{/* The elusion image reference: registry/repository@digest when a digest is set, else :tag. */}}
{{- define "elusion.image" -}}
{{- $i := .Values.image -}}
{{- if $i.requireDigest -}}
{{- $_ := required "image.digest is required on AKS: --set image.digest=sha256:... (the digest printed by the push)" $i.digest -}}
{{- $_ := required "image.registry is required on AKS: --set image.registry=$(terraform output -raw acr_login_server)" $i.registry -}}
{{- end -}}
{{- $repo := ternary (printf "%s/%s" $i.registry $i.repository) $i.repository (ne $i.registry "") -}}
{{- if $i.digest -}}{{ $repo }}@{{ $i.digest }}{{- else -}}{{ $repo }}:{{ $i.tag }}{{- end -}}
{{- end }}
