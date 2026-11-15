terraform {
  required_version = ">= 1.17.1"
  required_providers {
    azurerm = {
      source  = "hashicorp/azurerm"
      version = "= 5.14.0"
    }
  }
}

# The subscription comes from ARM_SUBSCRIPTION_ID (or the az CLI default), never from a
# committed file. Sign-in is the owner's `az login`.
provider "azurerm" {
  features {}

  # 5.x registers nothing by default. These are the providers the phase uses; the first
  # five and Consumption were registered by hand in task 0, ManagedIdentity is needed for
  # the kubelet identity AKS creates. Registration happens when the provider starts (also
  # during `plan`), is free, and is not undone by `destroy`.
  resource_provider_registrations = "none"
  resource_providers_to_register = [
    "Microsoft.Compute",
    "Microsoft.Consumption",
    "Microsoft.ContainerRegistry",
    "Microsoft.ContainerService",
    "Microsoft.ManagedIdentity",
    "Microsoft.Network",
    "Microsoft.Storage",
  ]
}
