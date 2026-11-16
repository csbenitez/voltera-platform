#!/usr/bin/env bash
# Siembra los datos de demo en los cuatro retos. Requiere el stack arriba: docker compose --profile todo up -d
set -euo pipefail
RAIZ="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$RAIZ"
set -a; . ./.env; set +a
R3="$RAIZ/retos/r3-blockchain"
PY="${PYTHON:-$RAIZ/retos/r4-agentes-ia/.venv/Scripts/python}"
[ -x "$PY" ] || PY="$RAIZ/retos/r4-agentes-ia/.venv/bin/python"

echo "==> R3: contrato y anclaje de facturas"
[ -d "$R3/node_modules" ] || (cd "$R3" && npm ci --no-audit --no-fund)
[ -f "$R3/deployments/e1.json" ] || (cd "$R3/contracts-hardhat" && npx hardhat run scripts/deploy-e1.js --network besu)
(cd "$R3/contracts-hardhat" && NODE_PATH="$R3/node_modules" npx hardhat run "$RAIZ/seed/anclar-facturas.js" --network besu)
docker compose --profile cadena up -d --force-recreate invoice-verifier

echo "==> R2: lecturas de medidores (calces de energía)"
docker compose --profile inyector run --rm injector | grep -E "Inyectadas|error" || true

echo "==> R4 + R1: CRM, pagos e índice del RAG"
"$PY" seed/cargar_copiloto.py
(cd retos/r4-agentes-ia && "$PY" -m copiloto.ingesta)
echo "Listo."
