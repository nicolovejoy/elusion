variable "location" {
  description = "Azure region for every resource."
  type        = string
  default     = "westus2"
}

variable "name" {
  description = "Base name: the resource group is rg-NAME and the cluster aks-NAME (the chart's runEnv in values-aks.yaml)."
  type        = string
  default     = "elusion"
}

variable "kubernetes_version" {
  description = "AKS Kubernetes minor version; AKS picks its latest patch. 1.36 is what the rehearsal ran on (OrbStack 1.36.5) and is within one minor of kubectl 1.37."
  type        = string
  default     = "1.36"
}

variable "node_count" {
  description = "Nodes in the one node pool."
  type        = number
  default     = 2
}

variable "node_size" {
  description = "VM size of the nodes. Standard_D2as_v7: 2 vCPU, 8 GB, x86-64 (DECISIONS 2026-11-15). Changing it rotates the pool through a temporary one."
  type        = string
  default     = "Standard_D2as_v7"
}

variable "budget_amount" {
  description = "Monthly budget in USD, for each of the two budgets."
  type        = number
  default     = 10
}

variable "budget_email" {
  description = "Address the budget alerts go to. No default: set TF_VAR_budget_email in the shell, so it is in no file."
  type        = string
  sensitive   = true
}
