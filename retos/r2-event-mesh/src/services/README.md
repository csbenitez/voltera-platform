# src/services/

Aquí solo queda `match-processor/`, el experimento de idempotencia (independiente, ver
[`docs/IDEMPOTENCY_EXPERIMENT.md`](../../docs/IDEMPOTENCY_EXPERIMENT.md)).

Los mocks del lab EDA viven en [`services/`](../../services). Es un módulo Go único con una imagen
`voltera-eda`:

| Binario | Rol (ARCH.md) |
|---|---|
| `declaration-gateway` | Ingress queue → SQLite + outbox → `PositionDeclared` (KEDA 0..N) |
| `price-feed-simulator` | Emite `Precio kWh` cada `PRICE_INTERVAL` |
| `matching-engine` | Proyector CQRS (Redis TTL) + calce FCFS → `EnergyMatched` + heartbeat |
| `energy-transfer` | Consume `EnergyMatched` → logs `SWITCH_CMD` |
| `mesh-bridge` | On-prem → Event Mesh (HTTPS + token) |
| `mesh-router` | Event Mesh central: dedup, enrutamiento a Facturación, failover |
| `facturacion` | Mock externo: valida y registra cierres de calce (`/stats`, `/invoices`) |
| `injector` | Inyección sintética de N declaraciones AMI |

Correr un binario local contra un Redpanda accesible:
`cd services && KAFKA_BROKERS=localhost:9092 REGION=onprem go run ./cmd/price-feed-simulator`.
