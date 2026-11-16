# Phase 6: everything the AKS run needs, and nothing else (docs/plans/phase-6-production.md,
# task 7). Created by the owner's `terraform apply`, removed by `terraform destroy`.

data "azurerm_client_config" "current" {}

locals {
  # Registry and storage account names are global across Azure, so they carry a short
  # suffix derived from the subscription: unique per subscription, stable across applies,
  # and not the subscription id itself.
  suffix = substr(sha1(data.azurerm_client_config.current.subscription_id), 0, 6)
  tags   = { project = "elusion", phase = "6" }
}

resource "azurerm_resource_group" "main" {
  name     = "rg-${var.name}"
  location = var.location
  tags     = local.tags
}

# The cluster. Free control plane, one node pool, no availability zones.
resource "azurerm_kubernetes_cluster" "main" {
  name                = "aks-${var.name}"
  location            = azurerm_resource_group.main.location
  resource_group_name = azurerm_resource_group.main.name
  dns_prefix          = var.name
  sku_tier            = "Free"
  kubernetes_version  = var.kubernetes_version

  # No automatic upgrades of any kind on a cluster that lives for a day: a node image
  # update would drain a node under a running review.
  node_os_upgrade_channel = "None"

  default_node_pool {
    name       = "system"
    vm_size    = var.node_size
    node_count = var.node_count
    # The v7 sizes attach disks through NVMe only and need an NVMe-capable image; Ubuntu
    # 24.04 is one, and the `Ubuntu` default moves to 26.04 from Kubernetes 1.38, which
    # is not (DECISIONS 2026-11-15). The size has no ephemeral OS disk.
    os_sku       = "Ubuntu2404"
    os_disk_type = "Managed"
    # Lets a change of node_size rotate the pool instead of replacing the cluster.
    temporary_name_for_rotation = "rotate"
  }

  identity {
    type = "SystemAssigned"
  }

  # Required block in azurerm 5.x. Manual: the node pool above is the only one; no node
  # auto-provisioning.
  node_provisioning_profile {
    mode = "Manual"
  }

  storage_profile {
    blob_driver_enabled = true # the corpus volume is the blob container (call 11)
  }

  network_profile {
    network_plugin      = "azure"
    network_plugin_mode = "overlay"
  }

  tags = local.tags
}

# The image registry. The kubelet identity pulls; no admin user, no password.
resource "azurerm_container_registry" "main" {
  name                = "acr${var.name}${local.suffix}"
  location            = azurerm_resource_group.main.location
  resource_group_name = azurerm_resource_group.main.name
  sku                 = "Basic"
  admin_enabled       = false
  tags                = local.tags
}

resource "azurerm_role_assignment" "kubelet_acr_pull" {
  scope                            = azurerm_container_registry.main.id
  role_definition_name             = "AcrPull"
  principal_id                     = azurerm_kubernetes_cluster.main.kubelet_identity[0].object_id
  principal_type                   = "ServicePrincipal"
  skip_service_principal_aad_check = true
}

# The corpus: one private container, mounted into the flow-run pods by the Blob CSI
# driver with the kubelet identity (AzureStorageAuthType MSI). The driver never asks for
# the account key in that mode, and nothing else uses it.
resource "azurerm_storage_account" "corpus" {
  name                            = "st${var.name}${local.suffix}"
  location                        = azurerm_resource_group.main.location
  resource_group_name             = azurerm_resource_group.main.name
  account_tier                    = "Standard"
  account_replication_type        = "LRS"
  allow_nested_items_to_be_public = false
  min_tls_version                 = "TLS1_2"
  tags                            = local.tags
}

resource "azurerm_storage_container" "corpus" {
  name                  = "corpus"
  storage_account_id    = azurerm_storage_account.corpus.id
  container_access_type = "private"
}

resource "azurerm_role_assignment" "kubelet_corpus" {
  scope                            = azurerm_storage_container.corpus.id
  role_definition_name             = "Storage Blob Data Contributor"
  principal_id                     = azurerm_kubernetes_cluster.main.kubelet_identity[0].object_id
  principal_type                   = "ServicePrincipal"
  skip_service_principal_aad_check = true
}

# The owner, signed in with az, reads the container (and, on the fallback of call 13,
# uploads the Parquet file) with `--auth-mode login`, also without the account key.
resource "azurerm_role_assignment" "owner_corpus" {
  scope                = azurerm_storage_container.corpus.id
  role_definition_name = "Storage Blob Data Contributor"
  principal_id         = data.azurerm_client_config.current.object_id
}

# Two budgets: this resource group, and the node resource group AKS creates for the VMs,
# disks and load balancer, which is where most of the cost lands. Alerts arrive hours
# late; they are a backstop, not a meter.
locals {
  budgets = {
    main  = azurerm_resource_group.main.id
    nodes = azurerm_kubernetes_cluster.main.node_resource_group_id
  }
}

resource "azurerm_consumption_budget_resource_group" "main" {
  for_each          = local.budgets
  name              = "budget-${var.name}-${each.key}"
  resource_group_id = each.value
  amount            = var.budget_amount
  time_grain        = "Monthly"

  time_period {
    # The first of the current month (UTC), which Azure requires; fixed after creation.
    start_date = formatdate("YYYY-MM-01'T'00:00:00Z", timestamp())
  }

  notification {
    operator       = "GreaterThanOrEqualTo"
    threshold      = 50
    threshold_type = "Actual"
    contact_emails = [var.budget_email]
  }

  notification {
    operator       = "GreaterThanOrEqualTo"
    threshold      = 100
    threshold_type = "Actual"
    contact_emails = [var.budget_email]
  }

  lifecycle {
    ignore_changes = [time_period]
  }
}
