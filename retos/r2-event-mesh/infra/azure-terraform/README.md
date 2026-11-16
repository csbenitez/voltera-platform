# Región 2: Azure (Terraform)

Gestiona `rg-voltera-dev` (eastus2) para el lab EDA.

| Módulo | Recursos | Notas FinOps |
|---|---|---|
| `network` | RG, VNet, subnet AKS | — |
| `monitoring` | Log Analytics (cap 0.5 GB/día) + Application Insights | Destino de las trazas OTel de ambas regiones |
| `aks` | AKS SKU Free, 1× `Standard_B4ms`, Azure CNI | Se puede apagar (start/stop) |
| `acr` | Azure Container Registry Basic + AcrPull para la identidad kubelet | ~5 USD/mes; guarda la imagen `voltera/voltera-eda` |
| `finops_automation` | Automation Account + runbooks Start/Stop + schedule 20:00 COT | Apagado diario automático |

> Se eliminaron los módulos `apigateway` (APIM) y `eventgrid`: no aparecen en ARCH. El Event Mesh es el
> `mesh-router` detrás del Istio ingress. El primer `terraform apply` tras este cambio **destruye**
> APIM y Event Grid y **crea** el ACR.

## Uso

```powershell
az login
cd infra\azure-terraform\environments\dev
copy terraform.tfvars.example terraform.tfvars   # si no existe
terraform init
terraform plan          # revisar: + acr, + role assignment, - apim, - eventgrid
terraform apply
az aks get-credentials -g rg-voltera-dev -n voltera-dev-aks --overwrite-existing
..\..\..\..\scripts\lab.ps1 addons -Target cloud
```

Outputs que consume `scripts/lab.ps1`: `acr_login_server` y `app_insights_connection_string`.

## FinOps

```powershell
.\scripts\lab.ps1 aks -Action status|start|stop     # o infra\azure-terraform\scripts\aks-power.ps1
```
Detalle de costos en [`docs/FINOPS_AKS.md`](../../docs/FINOPS_AKS.md).
