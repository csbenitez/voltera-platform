# Runbook del lab EDA

Ejecución paso a paso del lab de [`ARCH.md`](ARCH.md), con la evidencia esperada en cada paso.
Los comandos están en PowerShell; el equivalente bash es `./scripts/lab.sh <cmd> <target>`.

## 0. Preparación (una vez)

| Paso | Comando | Evidencia |
|---|---|---|
| Cluster local | `.\infra\manage.ps1 local start` | `k3d cluster list` muestra `voltera-onprem` |
| AKS encendido | `.\scripts\lab.ps1 aks -Action start` | `power: Running` |
| Credenciales AKS | `.\infra\manage.ps1 azure connect` | existe el contexto `voltera-dev-aks` |
| Terraform | `.\infra\manage.ps1 azure plan`, luego `apply` | el plan **crea** ACR + AcrPull y **destruye** APIM + Event Grid |
| Limpieza de la arquitectura previa | `.\scripts\lab.ps1 cleanup -Target all` | no quedan los ns `knative-*`, `voltera-onprem` ni Istio local; `keda` está `Running` |
| Add-ons | `.\scripts\lab.ps1 addons -Target all` | pods `keda-operator` en ambos clusters; `istio-ingress` y `cert-manager` en AKS |

## 1. Despliegue

```powershell
.\scripts\lab.ps1 up -Target all
.\scripts\lab.ps1 status
```

Resultado esperado:
- En ambos clusters (ns `voltera`) corren `redpanda-0`, `redis`, `otel-collector`, `price-feed-simulator`,
  `matching-engine` y `energy-transfer`.
- `declaration-gateway` arranca con **0 réplicas** (lo maneja KEDA).
- On-prem tiene además `mesh-bridge`. La nube tiene además `mesh-router` y `facturacion`.
- Event Mesh: `state=NORMAL`, con `heartbeat_age` por debajo de 5 s.

## 2. Flujo de ejecución (ARCH §3)

```powershell
kubectl --context k3d-voltera-onprem -n voltera get pods -w    # en otra terminal
.\scripts\lab.ps1 inject -Target onprem -N 200 -Rate 50
```

| # | Paso ARCH | Evidencia |
|---|---|---|
| 1 | Inyección sintética | El injector imprime `Inyectadas 200 declaraciones` y los trace ids |
| 2 | Ingesta y outbox | KEDA escala `declaration-gateway` de 0 a N. Sus logs muestran `declaración persistida`. Vuelve a 0 unos 45 s después de que el lag llega a 0 |
| 3 | Proyección de mercado | Logs de `price-feed-simulator`: `precio publicado` cada 10 s |
| 4 | Emparejamiento | Logs de `matching-engine`: `EnergyMatched origin=onprem processed_by=onprem`. Logs de `energy-transfer`: `SWITCH_CMD` |
| 5 | Consolidación global | `lab.ps1 status`: Facturación con `by_route.onprem->onprem > 0`. El mesh muestra `billing_routed_onprem` |

### Dashboard de carga

```powershell
.\scripts\lab.ps1 dashboard      # port-forward a onprem y abre http://localhost:8090
```

El injector también corre como Deployment `injector-dashboard` (`injector -serve`) con un `index.html` para
lanzar y detener cargas contra el Event Mesh: `constante`, `rampa`, `rafaga` (escalado KEDA), `cruce-knative`
(medidores foráneos onprem → Knative → nube) y `soak`. Muestra enviados, errores, msg/s reales, latencia de
publish, trace ids (para `lab.ps1 trace`) e historial. El failover sigue en `lab.ps1 failover-test`.
Corre una carga a la vez; el Job `lab.ps1 inject` no cambia.

Repite con `-Target cloud` para la región 2. Facturación consolida `cloud->cloud` y `onprem->onprem` sin
duplicados (`duplicates: 0`).

## 3. Medición 1: traza de interoperabilidad

```powershell
.\scripts\lab.ps1 trace                      # usa el último trace id del injector
.\scripts\lab.ps1 trace -TraceId <id>
```

- Abre App Insights `voltera-dev-appinsights` → **Transaction search** → pega el trace id → **End-to-end
  transaction details**.
- Hay que ver los spans de `injector`, `declaration-gateway`, `matching-engine`, `energy-transfer` y
  `mesh-bridge` (región onprem), y luego `mesh-router` y `facturacion` (región cloud) bajo el mismo
  `operation_Id`.
- La ingesta en App Insights tarda entre 1 y 3 minutos.
- `Application Map` muestra la topología `mesh-bridge → mesh-router`.

## 4. Medición 2: failover

```powershell
.\scripts\lab.ps1 failover-test -N 100
```

Qué hace el script y qué debe pasar:
1. Verifica `NORMAL` y guarda `by_route.onprem->cloud`.
2. Escala `matching-engine` (onprem) a 0 e inyecta 100 declaraciones on-prem.
3. Unos 15 a 20 s después, el mesh pasa a `FAILOVER_ACTIVE`. El motivo queda como `sin heartbeat de onprem
   por 15s` y el contador `failover_replayed` es mayor que 0.
4. Facturación suma calces `onprem->cloud`. Los logs de `matching-engine` en la nube muestran
   `origin=onprem processed_by=cloud`.
5. Restaura el engine local y el mesh vuelve a `NORMAL` (`heartbeat de onprem recuperado`).
6. Imprime `PASS` y la tabla de transiciones.

Prueba manual equivalente:
`kubectl --context k3d-voltera-onprem -n voltera scale deploy/matching-engine --replicas=0` y luego
`inject`, `status`, y `--replicas=1`.

## 5. Variantes útiles

- **Nube caída:** `.\scripts\lab.ps1 aks -Action stop` o borrar el mesh-router. Los logs de `mesh-bridge`
  muestran reintentos y `rpk group describe mesh-bridge` muestra el lag acumulado. Al volver, el lag se
  drena sin pérdida.
- **Carga:** `inject -N 2000 -Rate 200` muestra cómo KEDA sube hasta 5 réplicas (6 particiones,
  `lagThreshold=20`).

## 6. Cierre (FinOps)

```powershell
.\scripts\lab.ps1 down -Target cloud     # opcional: libera Redpanda/pods en AKS
.\scripts\lab.ps1 aks -Action stop       # apaga el cómputo de AKS
.\infra\manage.ps1 local stop            # pausa k3d
```
