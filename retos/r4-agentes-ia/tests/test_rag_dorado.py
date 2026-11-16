"""Conjunto dorado del RAG. Es una prueba de integración: necesita copiloto-db arriba y el índice cargado
(`python -m copiloto.ingesta`). Si la base no responde, se omite."""
import psycopg
import pytest

from copiloto import rag

try:
    with psycopg.connect(rag.DB_RO, connect_timeout=2) as _c:
        _n = _c.execute("SELECT count(*) FROM fragmentos").fetchone()[0]
except Exception:
    _n = 0
pytestmark = pytest.mark.skipif(_n == 0, reason="copiloto-db sin índice")

# (pregunta, NIC del cliente en la llamada, fuente esperada entre los 3 primeros)
DORADO = [
    ("¿Cuánto cuesta el kWh comprado a un vecino?", None, "knowledge/tarifas-2026-10.md"),
    ("¿Por qué le subió la factura este mes al cliente?", None, "knowledge/tarifas-2026-10.md"),
    ("¿Qué significa la línea excedente con valor negativo?", None, "knowledge/tarifas-2026-10.md"),
    ("El cliente ya pagó y la factura sigue pendiente, ¿qué hago?", None, "knowledge/faq-pagos.md"),
    ("¿Se cobra dos veces si se reintenta el pago?", None, "knowledge/faq-pagos.md"),
    ("¿Cuándo vence la factura?", None, "knowledge/faq-pagos.md"),
    ("La verificación dio ALTERADA, ¿qué prioridad tiene el reclamo?", None, "knowledge/politica-reclamos.md"),
    ("¿En cuántos días se responde un reclamo de prioridad normal?", None, "knowledge/politica-reclamos.md"),
    ("La factura aparece como NO ANCLADA, ¿está mal?", None, "knowledge/politica-reclamos.md"),
    ("¿Qué evidencia de la cadena le entrego al cliente?", None, "knowledge/procedimiento-verificacion.md"),
    ("¿Qué quiere decir el motivo huella_distinta?", None, "knowledge/procedimiento-verificacion.md"),
    ("¿Cómo se convierten los calces de energía en la línea compra_p2p?", None, "knowledge/energia-p2p.md"),
    ("¿A qué precio se cobra un calce de energía?", None, "knowledge/energia-p2p.md"),
    ("¿Qué capacidad de paneles tiene instalada el titular?", "NIC-2001001", "contratos/NIC-2001001.md"),
    ("¿Con qué medio de pago paga el titular?", "NIC-2001002", "contratos/NIC-2001002.md"),
]


@pytest.mark.parametrize("pregunta,nic,esperada", DORADO)
def test_fuente_esperada_en_top3(pregunta, nic, esperada):
    fuentes = [f.fuente for f in rag.buscar(pregunta, nic=nic, top=3)]
    assert esperada in fuentes, fuentes


@pytest.mark.parametrize("nic", [None, "NIC-2001001", "NIC-2001003"])
def test_nunca_se_devuelve_el_contrato_de_otro_cliente(nic):
    for pregunta in ["contrato del titular plan paneles medio de pago", "NIC-2001002 José Ángel Núñez contrato"]:
        for f in rag.buscar(pregunta, nic=nic, top=20):
            assert f.nic in (None, nic), (f.fuente, nic)
