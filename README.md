# Voltera Platform

Monorepo de los cuatro retos del caso Voltera (ARTI-4208).

| Reto | Carpeta | Qué es |
|---|---|---|
| R1 Service mesh | `retos/r1-service-mesh` | Pagos con Istio, idempotencia y resiliencia |
| R2 Event mesh | `retos/r2-event-mesh` | EDA sobre Redpanda y event mesh multirregión |
| R3 Blockchain | `retos/r3-blockchain` | Facturas ancladas en Besu QBFT e `invoice-verifier` |
| R4 Agentes de IA | `retos/r4-agentes-ia` | Copiloto del call center de facturación: RAG híbrido, MCP propio y grafo LangGraph |

## Arranque de la demo

```bash
cp .env.example .env            # tokens, LLAVE_DOCUMENTOS_HEX (64 hex) y la llave del LLM
docker compose --profile todo up -d --build
bash scripts/seed.sh            # contrato, facturas ancladas, calces, CRM, pagos e índice RAG
```

Copiloto: http://localhost:8501 · MCP: http://localhost:18000/mcp · por consola: `python -m copiloto.cli "..."` desde `retos/r4-agentes-ia`.

Clientes de demo: `NIC-2001001` (factura alta), `NIC-2001002` (pagó y sigue pendiente), `NIC-2001003` (factura alterada), `NIC-2001004` (sin anclar).

El LLM se elige con `LLM_PROVIDER` y `LLM_MODEL` (ver `retos/r4-agentes-ia/copiloto/llm.py`); `.env.example` trae la configuración para Azure AI Foundry.

Arquitectura: [docs/arquitectura-unificada.md](docs/arquitectura-unificada.md) · [ADR del tipo de RAG](docs/adr/0001-tipo-de-rag.md)

Pruebas del copiloto: `pytest` en `retos/r4-agentes-ia`.
