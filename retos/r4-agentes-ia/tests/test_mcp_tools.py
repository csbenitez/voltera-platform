import httpx
import pytest
import respx

from mcp_server import config as C
from mcp_server import server as S
from mcp_server import validacion as V


def test_validacion_rechaza_ids_malformados():
    assert V.nic("NIC-2001001")
    for malo in ["NIC-1", "nic-2001001", "NIC-2001001; DROP TABLE x", ""]:
        with pytest.raises(ValueError):
            V.nic(malo)
    with pytest.raises(ValueError):
        V.factura("FAC-2026-10-1")
    with pytest.raises(ValueError):
        V.medidor("AMI-mars-0001")


def test_enmascarar_nic():
    assert V.enmascarar_nic("NIC-2001001") == "NIC-***01"


@respx.mock
def test_verificador_ok_devuelve_veredicto_con_evidencia():
    respx.get(f"{C.VERIFIER_URL}/facturas/FAC-2026-10-0000001/verificacion").mock(
        return_value=httpx.Response(200, json={"idNegocio": "FAC-2026-10-0000001", "resultado": "ALTERADA",
                                               "motivo": "huella_distinta", "evidencia": {"txHash": "0xabc"}}))
    r = S.cadena_verificar_factura("FAC-2026-10-0000001")
    assert r["veredicto"] == "ALTERADA" and r["evidencia"]["txHash"] == "0xabc"


@respx.mock
def test_cadena_caida_no_se_reporta_como_alterada():
    respx.get(f"{C.VERIFIER_URL}/facturas/FAC-2026-10-0000001/verificacion").mock(
        return_value=httpx.Response(503, json={"error": "cadena"}))
    assert S.cadena_verificar_factura("FAC-2026-10-0000001")["veredicto"] == "SIN_VEREDICTO"
    respx.get(f"{C.VERIFIER_URL}/facturas/FAC-2026-10-0000002/verificacion").mock(side_effect=httpx.ConnectError("x"))
    assert S.cadena_verificar_factura("FAC-2026-10-0000002")["veredicto"] == "SIN_VEREDICTO"


@respx.mock
def test_calces_filtra_por_medidor():
    respx.get(f"{C.FACTURACION_URL}/invoices").mock(return_value=httpx.Response(200, json=[
        {"seller_meter": "AMI-cloud-0001", "buyer_meter": "AMI-cloud-0009", "kwh": 2.5},
        {"seller_meter": "AMI-cloud-0005", "buyer_meter": "AMI-cloud-0001", "kwh": 1.0},
        {"seller_meter": "AMI-cloud-0007", "buyer_meter": "AMI-cloud-0008", "kwh": 9.0},
    ]))
    r = S.eventos_calces_por_medidor("AMI-cloud-0001")
    assert r["calces"] == 2 and r["kwh_vendidos"] == 2.5 and r["kwh_comprados"] == 1.0


def test_pagos_por_factura_usa_parametros(monkeypatch):
    visto = {}
    monkeypatch.setattr(S, "_sql", lambda url, q, p=(): visto.update(q=q, p=p) or [])
    S.pagos_por_factura("FAC-2026-10-0000001")
    assert visto["p"] == ("FAC-2026-10-0000001",) and "%s" in visto["q"]
    with pytest.raises(ValueError):
        S.pagos_por_factura("x' OR '1'='1")


@respx.mock
def test_registrar_pago_envia_clave_de_idempotencia_y_centavos():
    ruta = respx.post(f"{C.OPERATOR_URL}/v1/payments").mock(
        return_value=httpx.Response(201, json={"id": "p1", "status": "COMPLETED"}))
    S.pagos_registrar_pago("FAC-2026-10-0000002", "participant-bank", "participant-card", 1500, "k-1")
    req = ruta.calls[0].request
    assert req.headers["Idempotency-Key"] == "k-1"
    assert b'"amount_minor":150000' in req.content.replace(b" ", b"")
