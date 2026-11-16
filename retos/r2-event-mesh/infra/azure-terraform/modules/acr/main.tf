# Azure Container Registry Basic (~5 USD/mes) para la imagen única voltera-eda.
# AKS la descarga con su identidad kubelet (AcrPull), sin credenciales en el cluster.
locals {
  # Sufijo determinista (los nombres de ACR son globales) sin depender del provider random.
  suffix = substr(md5("${var.resource_group_name}-${var.location}"), 0, 6)
}

resource "azurerm_container_registry" "acr" {
  name                = "${replace(var.prefix, "-", "")}${local.suffix}acr"
  resource_group_name = var.resource_group_name
  location            = var.location
  sku                 = "Basic"
  admin_enabled       = false
  tags                = var.tags
}

resource "azurerm_role_assignment" "aks_acr_pull" {
  scope                            = azurerm_container_registry.acr.id
  role_definition_name             = "AcrPull"
  principal_id                     = var.kubelet_object_id
  skip_service_principal_aad_check = true
}
