# Add-ons

| Add-on | Local (k3d) | AKS | Uso en el lab |
|---|---|---|---|
| KEDA `2.16.0` | ✅ | ✅ | ScaledObject Kafka (lag) → `declaration-gateway` 0..5 |
| Istio `1.24.2` (base + istiod + gateway) | — | ✅ | Solo **edge** del Event Mesh (IP pública, DNS `voltera-dev-mesh`), sin sidecars |
| cert-manager `v1.16.2` | — | ✅ | Certificado Let's Encrypt del edge (`deploy/cloud-edge`) |

Las versiones están en [`versions.env`](versions.env). Redpanda, Redis y el OTel Collector se despliegan como
parte de la aplicación (`deploy/base`), no como add-ons.

```powershell
.\infra\addons\install.ps1 -Target local             # contexto k3d-voltera-onprem
.\infra\addons\install.ps1 -Target azure             # contexto voltera-dev-aks
# o bien: .\scripts\lab.ps1 addons -Target all
```
```bash
./infra/addons/install.sh --target local|azure [--context <ctx>]
```
