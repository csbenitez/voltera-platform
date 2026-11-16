output "resource_group_name" {
  description = "Nombre del Resource Group"
  value       = module.network.resource_group_name
}

output "aks_cluster_name" {
  description = "Nombre del clúster AKS"
  value       = module.aks.cluster_name
}

output "aks_connect_command" {
  description = "Comando para configurar kubectl con el clúster AKS"
  value       = "az aks get-credentials --resource-group ${module.network.resource_group_name} --name ${module.aks.cluster_name} --overwrite-existing"
}

output "acr_name" {
  description = "Nombre del Azure Container Registry"
  value       = module.acr.name
}

output "acr_login_server" {
  description = "Login server del ACR (imagen voltera-eda)"
  value       = module.acr.login_server
}

output "app_insights_connection_string" {
  description = "Connection String de Application Insights"
  value       = module.monitoring.app_insights_connection_string
  sensitive   = true
}

output "finops_stop_command" {
  description = "Comando rápido de PowerShell para apagar el clúster AKS y suspender el cómputo"
  value       = "az aks stop --resource-group ${module.network.resource_group_name} --name ${module.aks.cluster_name} --no-wait"
}

output "finops_start_command" {
  description = "Comando rápido de PowerShell para reanudar el clúster AKS"
  value       = "az aks start --resource-group ${module.network.resource_group_name} --name ${module.aks.cluster_name} --no-wait"
}
