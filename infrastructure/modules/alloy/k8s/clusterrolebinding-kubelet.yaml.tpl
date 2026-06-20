# =============================================================================
# Alloy ClusterRoleBinding — bind kubelet-scrape ClusterRole to chart's SA
# =============================================================================
# References the ServiceAccount created by the Alloy Helm chart's default RBAC.
# SA name follows the chart's standard `<release>` pattern.
# =============================================================================
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRoleBinding
metadata:
  name: ${name}
  labels:
    app.kubernetes.io/name: alloy
    app.kubernetes.io/managed-by: terraform
    app.kubernetes.io/component: kubelet-scrape-rbac
roleRef:
  apiGroup: rbac.authorization.k8s.io
  kind: ClusterRole
  name: ${role_name}
subjects:
  - kind: ServiceAccount
    name: ${service_account_name}
    namespace: ${namespace}
