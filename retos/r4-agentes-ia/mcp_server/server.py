"""Servidor MCP del copiloto de facturación. Cada herramienta pertenece a un reto:
crm_* -> base del copiloto · pagos_* -> R1 service mesh · eventos_* -> R2 event mesh · cadena_* -> R3 blockchain.
Las lecturas usan roles de solo lectura; las escrituras (crm_crear_reclamo, pagos_registrar_pago) las
invoca el grafo únicamente tras la aprobación humana."""
import logging
import uuid

import httpx
import psycopg
from mcp.server.fastmcp import FastMCP
from psycopg.rows import dict_row

from . import config as C
from . import validacion as V

log = logging.getLogger("mcp")
mcp = FastMCP("voltera-copiloto", host="0.0.0.0", port=8000)


def _sql(url: str, query: str, params: tuple = ()):
    with psycopg.connect(url, row_factory=dict_row, options="-c default_transaction_read_only=on", connect_timeout=5) as con:
        return con.execute(query, params).fetchall()


def _get(url: str):
    return httpx.get(url, timeout=C.HTTP_TIMEOUT)


# ---------------- CRM (base del copiloto) ----------------
@mcp.tool()
def crm_buscar_cliente(nic: str) -> dict:
    """Datos del cliente por NIC: nombre, dirección, medidor, participante de pagos y comunidad."""
    nic = V.nic(nic)
    filas = _sql(C.COPILOTO_DB_RO, "SELECT nic,nombre,direccion,medidor,participant_id,comunidad FROM clientes WHERE nic=%s", (nic,))
    return filas[0] if filas else {"error": "cliente_no_encontrado", "nic": nic}


@mcp.tool()
def crm_facturas_de_cliente(nic: str) -> list[dict]:
    """Facturas del cliente (más recientes primero) con totales y líneas de cobro."""
    nic = V.nic(nic)
    return _sql(C.COPILOTO_DB_RO, "SELECT id_negocio,periodo,wh,cop,lineas,estado_cobro,anclada FROM facturas WHERE nic=%s ORDER BY periodo DESC, id_negocio DESC", (nic,))


@mcp.tool()
def crm_reclamos_de_cliente(nic: str) -> list[dict]:
    """Reclamos abiertos del cliente."""
    nic = V.nic(nic)
    return _sql(C.COPILOTO_DB_RO, "SELECT id,id_negocio,motivo,prioridad,creado_en FROM reclamos WHERE nic=%s ORDER BY id DESC", (nic,))


# ---------------- R1 · Service mesh (pagos) ----------------
@mcp.tool()
def pagos_participante(participant_id: str) -> dict:
    """Instrumento y pasarela con la que el participante paga (tarjeta, transferencia, billetera)."""
    pid = V.participante(participant_id)
    r = _get(f"{C.PARTICIPANT_URL}/v1/participants/{pid}")
    return r.json() if r.status_code == 200 else {"error": f"participant-manager {r.status_code}"}


@mcp.tool()
def pagos_por_factura(id_negocio: str) -> list[dict]:
    """Pagos registrados cuya referencia es la factura (estado, pasarela, monto en centavos, referencia del proveedor)."""
    fac = V.factura(id_negocio)
    return _sql(
        C.ROUTER_DB_RO,
        "SELECT id::text,status,gateway,amount_minor,currency,debtor_participant_id,creditor_participant_id,"
        "provider_ref,error,created_at FROM payments WHERE reference=%s ORDER BY created_at",
        (fac,),
    )


@mcp.tool()
def pagos_consultar_pago(payment_id: str) -> dict:
    """Consulta un pago por id en payment-operator."""
    try:
        uuid.UUID(payment_id)
    except ValueError:
        raise ValueError("payment_id inválido")
    r = _get(f"{C.OPERATOR_URL}/v1/payments/{payment_id}")
    return r.json() if r.status_code == 200 else {"error": f"payment-operator {r.status_code}"}


@mcp.tool()
def pagos_registrar_pago(id_negocio: str, deudor: str, acreedor: str, monto_cop: int, clave_idempotencia: str) -> dict:
    """ESCRITURA (requiere aprobación humana): registra un pago de la factura. Reintentar con la misma clave no duplica."""
    fac, deu, acr = V.factura(id_negocio), V.participante(deudor), V.participante(acreedor)
    if monto_cop <= 0:
        raise ValueError("monto inválido")
    r = httpx.post(
        f"{C.OPERATOR_URL}/v1/payments",
        timeout=C.HTTP_TIMEOUT,
        headers={"Idempotency-Key": clave_idempotencia},
        json={"debtor_participant_id": deu, "creditor_participant_id": acr, "amount_minor": monto_cop * 100,
              "currency": "COP", "reference": fac},
    )
    cuerpo = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
    return {"status_http": r.status_code, **cuerpo}


# ---------------- R2 · Event mesh (calces de energía) ----------------
@mcp.tool()
def eventos_calces_por_medidor(medidor: str) -> dict:
    """Calces de energía P2P recientes donde el medidor fue vendedor o comprador (últimos 100 de facturación)."""
    m = V.medidor(medidor)
    r = _get(f"{C.FACTURACION_URL}/invoices")
    if r.status_code != 200:
        return {"error": f"facturacion {r.status_code}"}
    mios = [x for x in r.json() if m in (x.get("seller_meter"), x.get("buyer_meter"))]
    return {
        "medidor": m,
        "calces": len(mios),
        "kwh_vendidos": round(sum(x["kwh"] for x in mios if x.get("seller_meter") == m), 3),
        "kwh_comprados": round(sum(x["kwh"] for x in mios if x.get("buyer_meter") == m), 3),
        "detalle": mios[:20],
    }


@mcp.tool()
def eventos_estadisticas() -> dict:
    """Totales del event mesh: facturas de calce registradas, inválidas, duplicadas y rutas."""
    r = _get(f"{C.FACTURACION_URL}/stats")
    return r.json() if r.status_code == 200 else {"error": f"facturacion {r.status_code}"}


# ---------------- R3 · Blockchain ----------------
@mcp.tool()
def cadena_verificar_factura(id_negocio: str) -> dict:
    """Recalcula la huella de la factura y la compara con la anclada en Besu. Veredictos: ÍNTEGRA, ALTERADA, NO ANCLADA.
    Si la cadena no responde devuelve SIN_VEREDICTO: nunca debe interpretarse como ALTERADA."""
    fac = V.factura(id_negocio)
    try:
        r = _get(f"{C.VERIFIER_URL}/facturas/{fac}/verificacion")
    except httpx.HTTPError as e:
        return {"veredicto": "SIN_VEREDICTO", "motivo": f"verificador inalcanzable: {type(e).__name__}"}
    if r.status_code != 200:
        return {"veredicto": "SIN_VEREDICTO", "motivo": f"verificador {r.status_code}"}
    d = r.json()
    return {"veredicto": d["resultado"], "motivo": d["motivo"], "evidencia": d.get("evidencia", {}), "idNegocio": d["idNegocio"]}


@mcp.tool()
def cadena_estado() -> dict:
    """Estado del verificador: contrato y último bloque."""
    try:
        r = _get(f"{C.VERIFIER_URL}/health")
        return r.json() if r.status_code == 200 else {"error": f"verificador {r.status_code}"}
    except httpx.HTTPError as e:
        return {"error": type(e).__name__}


# ---------------- Escritura CRM ----------------
@mcp.tool()
def crm_crear_reclamo(nic: str, id_negocio: str, motivo: str, prioridad: str = "normal") -> dict:
    """ESCRITURA (requiere aprobación humana): abre un reclamo sobre una factura."""
    nic, fac = V.nic(nic), V.factura(id_negocio)
    if prioridad not in ("normal", "alta"):
        raise ValueError("prioridad inválida")
    if not motivo.strip() or len(motivo) > 500:
        raise ValueError("motivo inválido")
    with psycopg.connect(C.COPILOTO_DB_RW, row_factory=dict_row, connect_timeout=5) as con:
        fila = con.execute(
            "INSERT INTO reclamos(nic,id_negocio,motivo,prioridad) VALUES (%s,%s,%s,%s) RETURNING id,creado_en",
            (nic, fac, motivo.strip(), prioridad),
        ).fetchone()
    log.info("reclamo creado nic=%s", V.enmascarar_nic(nic))
    return {"reclamo_id": fila["id"], "creado_en": fila["creado_en"].isoformat()}


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    mcp.run(transport="streamable-http")
