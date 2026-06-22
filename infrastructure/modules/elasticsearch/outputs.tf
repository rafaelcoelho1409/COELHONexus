# =============================================================================
# elasticsearch module — outputs
# =============================================================================

output "namespace" {
  description = "Namespace where ES + Kibana CRs live."
  value       = kubernetes_namespace_v1.elasticsearch.metadata[0].name
}

output "operator_namespace" {
  description = "Namespace where the ECK operator pod lives."
  value       = var.operator_namespace
}

output "operator_chart_version" {
  description = "Installed eck-operator chart version."
  value       = helm_release.eck_operator.version
}

output "stack_chart_version" {
  description = "Installed eck-stack chart version."
  value       = helm_release.eck_stack.version
}

output "es_version" {
  description = "Elasticsearch app version deployed."
  value       = var.es_version
}

# -----------------------------------------------------------------------------
# In-cluster endpoint — Nexus FastAPI uses this
# -----------------------------------------------------------------------------
# ECK creates Service `elasticsearch-es-http` (the eck-stack chart's
# fullnameOverride forces `elasticsearch` as the resource name).
# -----------------------------------------------------------------------------

output "in_cluster_url" {
  description = "Elasticsearch HTTPS URL for in-cluster apps. ECK auto-generates a CA — apps must trust it OR set verify_certs=False."
  value       = "https://elasticsearch-es-http.${var.namespace}.svc.cluster.local:9200"
}

output "external_url" {
  description = "Elasticsearch HTTPS URL via Tailscale Ingress. Tailscale terminates TLS with an LE-issued cert, then forwards to the in-cluster ES Service over HTTPS. Use this from laptop during Nexus dev."
  value       = "https://${var.tailscale_hostname_es}.${var.tailscale_domain}"
}

output "username" {
  description = "Built-in admin username."
  value       = "elastic"
}

output "password_secret_name" {
  description = "K8s Secret holding the effective `elastic` user password. Key: `elastic`."
  value       = local.elastic_password_override_enabled ? kubernetes_secret_v1.elastic_admin[0].metadata[0].name : "elasticsearch-es-elastic-user"
  sensitive   = true
}

output "ca_secret_name" {
  description = "K8s Secret holding the auto-generated CA cert. Key: `ca.crt`. Mount into apps for proper TLS verification."
  value       = "elasticsearch-es-http-certs-public"
}

output "password_retrieval_command" {
  description = "kubectl one-liner that prints the effective elastic user password."
  value       = local.elastic_password_override_enabled ? "kubectl get secret -n ${var.namespace} ${kubernetes_secret_v1.elastic_admin[0].metadata[0].name} -o jsonpath='{.data.elastic}' | base64 -d" : "kubectl get secret -n ${var.namespace} elasticsearch-es-elastic-user -o jsonpath='{.data.elastic}' | base64 -d"
  sensitive   = true
}

# -----------------------------------------------------------------------------
# External (Tailnet) — Kibana only
# -----------------------------------------------------------------------------

output "kibana_url" {
  description = "Kibana UI URL (HTTPS via Tailscale)."
  value       = "https://${var.tailscale_hostname_kibana}.${var.tailscale_domain}"
}

output "ready" {
  description = "Helm release status for the eck-stack chart."
  value       = helm_release.eck_stack.status
}
