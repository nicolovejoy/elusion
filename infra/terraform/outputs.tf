# What the Azure day's commands need (infra/README.md): `az aks get-credentials`, the image
# push, and the `--set` values of `helm install` (values-aks.yaml leaves them empty).

output "resource_group" {
  value = azurerm_resource_group.main.name
}

output "cluster_name" {
  value = azurerm_kubernetes_cluster.main.name
}

output "node_resource_group" {
  value = azurerm_kubernetes_cluster.main.node_resource_group
}

output "acr_name" {
  value = azurerm_container_registry.main.name
}

output "acr_login_server" {
  value = azurerm_container_registry.main.login_server
}

output "storage_account" {
  value = azurerm_storage_account.corpus.name
}

output "storage_container" {
  value = azurerm_storage_container.corpus.name
}

output "kubelet_client_id" {
  description = "corpus.blob.identityClientId: the identity the blob mount uses."
  value       = azurerm_kubernetes_cluster.main.kubelet_identity[0].client_id
}
