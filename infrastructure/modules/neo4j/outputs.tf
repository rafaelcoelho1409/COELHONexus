# =============================================================================
# neo4j module — outputs
# =============================================================================

output "namespace" {
  description = "Namespace where Neo4j is installed."
  value       = kubernetes_namespace_v1.neo4j.metadata[0].name
}

output "chart_version" {
  description = "Installed Helm chart version."
  value       = helm_release.neo4j.version
}

output "app_version" {
  description = "Neo4j app version (matches chart's appVersion)."
  value       = helm_release.neo4j.metadata.app_version
}

# -----------------------------------------------------------------------------
# In-cluster endpoints — apps point here (no TLS overhead)
# -----------------------------------------------------------------------------

output "in_cluster_bolt_url" {
  description = "Bolt URL for in-cluster apps (Nexus FastAPI). Plain bolt:// since traffic stays inside the cluster — no TLS needed."
  value       = "bolt://${var.release_name}.${var.namespace}.svc.cluster.local:7687"
}

output "in_cluster_http_url" {
  description = "Browser/REST URL in-cluster (port 7474)."
  value       = "http://${var.release_name}.${var.namespace}.svc.cluster.local:7474"
}

output "username" {
  description = "Built-in Neo4j admin username."
  value       = "neo4j"
}

# -----------------------------------------------------------------------------
# External (Tailnet) URLs — both with Tailscale-issued LE certs
# -----------------------------------------------------------------------------

output "browser_url" {
  description = "Browser UI URL (HTTPS, LE cert)."
  value       = "https://${var.tailscale_hostname_browser}.${var.tailscale_domain}"
}

output "bolt_url_for_browser" {
  description = "Bolt URL to enter in the Browser's connection field. Port 443 implicit (Tailscale Ingress listens there). LE-trusted — no `+ssc` needed."
  value       = "bolt+s://${var.tailscale_hostname_bolt}.${var.tailscale_domain}"
}

output "ready" {
  description = "Helm release status string ('deployed' on success)."
  value       = helm_release.neo4j.status
}
