# k8s/ — Experimento de idempotencia (independiente)

Esta carpeta contiene **solo** los manifiestos del experimento de idempotencia del match-processor
(PR #1):

| Archivo | Recursos |
|---|---|
| `match-processor.yaml` | Deployment/Service `match-processor`, Trigger de Knative y ScaledObject de KEDA (Prometheus) |
| `postgres-matches.yaml` | PostgreSQL con las tablas `matches` y `processed_events` |
| `redis-idempotency.yaml` | Redis de deduplicación |

> Dependen de **Knative Eventing** (Trigger) y de Prometheus, que ya no se instalan en el lab EDA. Ver
> [`docs/IDEMPOTENCY_EXPERIMENT.md`](../docs/IDEMPOTENCY_EXPERIMENT.md) y `scripts/idempotency/`.

Los manifiestos del lab EDA están en [`deploy/`](../deploy) (kustomize: `base`, `onprem`, `cloud`,
`cloud-edge`, `jobs`) y se aplican con `scripts/lab.ps1 deploy`.
