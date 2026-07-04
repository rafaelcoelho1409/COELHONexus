# =============================================================================
# k3d_expose module — NodePort Service for local-only browser/dashboard access
# =============================================================================
# Generic, cluster-agnostic: creates ONE additional Service (type=NodePort)
# targeting the same pods as an existing ClusterIP Service, so a human on
# their own laptop can reach a data-store's web UI or wire protocol (Neo4j
# Browser + Bolt, Qdrant Dashboard, etc.) from outside the cluster. Takes a
# LIST of ports — some services (Neo4j: HTTP Browser UI + Bolt driver
# protocol) need more than one port reachable to actually work end-to-end;
# exposing only the UI port lets the page load but breaks the login step,
# since Neo4j Browser's JS opens its own separate Bolt connection.
#
# Deliberately does NOT touch k3d itself (no `k3d cluster edit --port-add`,
# no null_resource). Wiring each NodePort through to an actual localhost
# address is a separate, manual step — see infrastructure/modules/k3d/main.tf
# for the fresh-install path. Keeping this module Kubernetes-only means it's
# inert-but-harmless on any cluster type, not just k3d — a caller on a
# cluster where NodePort->localhost isn't wired up (e.g. one that uses
# external Ingress instead) just never sets `enable_local_expose`, so this
# module is never even instantiated there.
#
# Does NOT modify or replace the target module's existing ClusterIP Service —
# Kubernetes allows multiple Services with different selectors/types to point
# at the same underlying pods. In-cluster DNS (`<release>.<namespace>.svc.
# cluster.local`) is completely unaffected by this module's existence.
# =============================================================================

resource "kubernetes_service_v1" "nodeport" {
  metadata {
    name      = "${var.service_name}-local"
    namespace = var.namespace
    labels = {
      "app.kubernetes.io/name"       = var.service_name
      "app.kubernetes.io/component"  = "local-expose"
      "app.kubernetes.io/managed-by" = "terraform"
    }
  }

  spec {
    selector = var.pod_selector
    type     = "NodePort"

    dynamic "port" {
      for_each = var.ports
      content {
        name        = port.value.name
        port        = port.value.target_port
        target_port = port.value.target_port
        node_port   = port.value.node_port
        protocol    = "TCP"
      }
    }
  }
}
