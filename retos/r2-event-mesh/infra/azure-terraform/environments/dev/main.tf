# 1. Red Base y Resource Group
module "network" {
  source = "../../modules/network"

  resource_group_name = var.resource_group_name
  location            = var.location
  prefix              = var.prefix
  tags                = var.tags
}

# 2. Observabilidad FinOps (Log Analytics Cap 0.5 GB/día + App Insights)
module "monitoring" {
  source = "../../modules/monitoring"

  resource_group_name = module.network.resource_group_name
  location            = module.network.location
  prefix              = var.prefix
  daily_quota_gb      = var.daily_quota_gb
  tags                = var.tags
}

# 3. Clúster AKS (Free SKU + 1 nodo B4ms: Istio edge + KEDA + Redpanda + mocks)
module "aks" {
  source = "../../modules/aks"

  resource_group_name        = module.network.resource_group_name
  location                   = module.network.location
  prefix                     = var.prefix
  subnet_id                  = module.network.aks_subnet_id
  node_vm_size               = var.node_vm_size
  node_count                 = var.node_count
  enable_auto_scaling        = var.enable_auto_scaling
  max_pods                   = var.max_pods
  log_analytics_workspace_id = module.monitoring.workspace_id
  tags                       = var.tags
}

# 4. Azure Container Registry Basic (imagen voltera-eda) + AcrPull para AKS
module "acr" {
  source = "../../modules/acr"

  resource_group_name = module.network.resource_group_name
  location            = module.network.location
  prefix              = var.prefix
  kubelet_object_id   = module.aks.kubelet_object_id
  tags                = var.tags
}

# 5. FinOps Automation (Runbooks y Schedule para apagar AKS automáticamente)
module "finops_automation" {
  source = "../../modules/finops_automation"

  resource_group_name = module.network.resource_group_name
  location            = module.network.location
  prefix              = var.prefix
  aks_cluster_id      = module.aks.cluster_id
  aks_cluster_name    = module.aks.cluster_name
  tags                = var.tags
}
