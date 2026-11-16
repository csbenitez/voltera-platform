# Región 1: on-prem simulado (k3d)

Es un cluster k3s de un solo nodo dentro de Docker Desktop: `voltera-onprem`, contexto
`k3d-voltera-onprem`, con Traefik deshabilitado. Simula la región on-prem del lab EDA.

```powershell
.\infra\manage.ps1 local start      # crea o reanuda el cluster (setup-k3s.ps1)
.\scripts\lab.ps1 addons -Target onprem
.\scripts\lab.ps1 up -Target onprem # build + k3d image import + secrets + deploy/onprem
.\infra\manage.ps1 local stop       # pausa (libera CPU/RAM)
.\infra\manage.ps1 local delete     # elimina el cluster
```

- **Qué corre aquí** (ns `voltera`): Redpanda, Redis, otel-collector, declaration-gateway (KEDA),
  price-feed-simulator, matching-engine, energy-transfer y mesh-bridge.
- **Imágenes:** no se usa registry. `lab.ps1 build` hace `k3d image import voltera/voltera-eda:<tag>`.
- **Conectividad:** solo saliente, hacia `https://voltera-dev-mesh.eastus2.cloudapp.azure.com/mesh/events`
  (mesh) y App Insights (trazas).
- **Recursos:** sin Istio ni Knative, el nodo queda holgado. Redpanda reserva unos 700 MB.

| Archivo | Descripción |
|---|---|
| `setup-k3s.ps1` | Verifica Docker y k3d, y crea o inicia el cluster |
| `teardown-k3s.ps1` | Detiene el cluster, o lo elimina con `-Delete` |
