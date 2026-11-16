"""Carga clientes y facturas en la base del copiloto y crea los participantes y pagos de demo en R1.

Requisitos: `anclar-facturas.js` ya generó seed/out/facturas.json y el stack `pagos` está arriba.
Uso: python seed/cargar_copiloto.py   (variables: COPILOTO_DB_ADMIN, OPERATOR_URL)
"""
import json
import os
import pathlib

import httpx
import psycopg

AQUI = pathlib.Path(__file__).parent
DB = os.environ.get("COPILOTO_DB_ADMIN", "postgresql://postgres:postgres@localhost:15432/copiloto")
OPERADOR = os.environ.get("OPERATOR_URL", "http://localhost:18080")

# Participante que cobra la factura (la comercializadora). Debe existir en el participant-manager.
ACREEDOR = "participant-bank"


def cargar_db(clientes, facturas):
    with psycopg.connect(DB) as con:
        con.execute("TRUNCATE reclamos, facturas, clientes CASCADE")
        for c in clientes:
            con.execute(
                "INSERT INTO clientes(nic,nombre,direccion,medidor,participant_id,comunidad,escenario) VALUES (%s,%s,%s,%s,%s,%s,%s)",
                (c["nic"], c["nombre"], c["direccion"], c["medidor"], c["participant_id"], c["comunidad"], c["escenario"]),
            )
        for f in facturas:
            con.execute(
                "INSERT INTO facturas(id_negocio,nic,periodo,wh,cop,lineas,estado_cobro,anclada,tx_hash) VALUES (%s,%s,%s,%s,%s,%s,'pendiente',%s,%s)",
                (f["idNegocio"], f["nic"], f["periodo"], f["wh"], f["cop"], json.dumps(f["lineas"]), f["anclada"], f["txHash"]),
            )
    print(f"CRM: {len(clientes)} clientes, {len(facturas)} facturas")


def crear_pagos(clientes, facturas):
    """Escenario 'pago_no_reflejado': el pago existe y está completado en R1, pero el CRM sigue en 'pendiente'."""
    por_nic = {c["nic"]: c for c in clientes}
    for f in facturas:
        c = por_nic[f["nic"]]
        if c["escenario"] != "pago_no_reflejado":
            continue
        r = httpx.post(
            f"{OPERADOR}/v1/payments",
            headers={"Idempotency-Key": f"seed-{f['idNegocio']}"},
            json={"debtor_participant_id": c["participant_id"], "creditor_participant_id": ACREEDOR,
                  "amount_minor": f["cop"] * 100, "currency": "COP", "reference": f["idNegocio"]},
            timeout=30,
        )
        print(f"R1: pago de {f['idNegocio']} -> HTTP {r.status_code} {r.text.strip()[:120]}")


if __name__ == "__main__":
    clientes = json.loads((AQUI / "clientes.json").read_text(encoding="utf-8"))
    facturas = json.loads((AQUI / "out" / "facturas.json").read_text(encoding="utf-8"))
    cargar_db(clientes, facturas)
    crear_pagos(clientes, facturas)
