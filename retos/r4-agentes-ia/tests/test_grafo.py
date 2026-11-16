"""Pruebas del grafo con un LLM simulado: enrutamiento, aislamiento de herramientas y aprobación humana."""
import asyncio
import json

from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.tools import StructuredTool
from langgraph.checkpoint.memory import MemorySaver
from langgraph.types import Command

from copiloto import grafo as G
from copiloto.rag import Fragmento


class LLMFalso:
    """guion(schema, tools, mensajes) -> instancia del esquema o AIMessage."""

    def __init__(self, guion, registro):
        self.guion, self.registro = guion, registro

    def with_structured_output(self, schema):
        return _Enlazado(self, schema=schema)

    def bind_tools(self, tools):
        return _Enlazado(self, tools=tools)

    async def ainvoke(self, mensajes):
        return await _Enlazado(self).ainvoke(mensajes)


class _Enlazado:
    def __init__(self, llm, schema=None, tools=None):
        self.llm, self.schema, self.tools = llm, schema, tools

    async def ainvoke(self, mensajes):
        if self.tools is not None:
            self.llm.registro.append(("tools", sorted(t.name for t in self.tools)))
        return self.llm.guion(self.schema, self.tools, mensajes)


def herramientas(llamadas):
    def crear(nombre, respuesta):
        async def f(**kw):
            llamadas.append((nombre, kw))
            return json.dumps(respuesta, ensure_ascii=False)
        return StructuredTool.from_function(coroutine=f, name=nombre, description=nombre, infer_schema=False,
                                            args_schema={"type": "object", "properties": {}, "additionalProperties": True})
    datos = {
        "crm_buscar_cliente": {"nic": "NIC-2001003", "participant_id": "participant-wallet", "medidor": "AMI-cloud-0003"},
        "crm_facturas_de_cliente": [{"id_negocio": "FAC-2026-10-0000003", "cop": 150000, "estado_cobro": "pendiente"}],
        "crm_reclamos_de_cliente": [], "pagos_participante": {}, "pagos_por_factura": [], "pagos_consultar_pago": {},
        "eventos_calces_por_medidor": {"calces": 3}, "eventos_estadisticas": {},
        "cadena_verificar_factura": {"veredicto": "ALTERADA", "motivo": "huella_distinta"}, "cadena_estado": {},
        "crm_crear_reclamo": {"reclamo_id": 7}, "pagos_registrar_pago": {"status_http": 201},
    }
    return [crear(n, r) for n, r in datos.items()]


def guion(plan, accion=None):
    def responder(schema, tools, mensajes):
        if schema is G.Plan:
            return G.Plan(especialistas=plan, pregunta="¿la factura es íntegra?")
        if schema is G.Suficiencia:
            return G.Suficiencia(suficiente=True)
        if schema is G.Sintesis:
            return G.Sintesis(respuesta="resumen", guion_cliente="guion", accion=accion)
        if tools is not None:
            if any(m.type == "tool" for m in mensajes):
                return AIMessage("veredicto ALTERADA para FAC-2026-10-0000003")
            nombre = "cadena_verificar_factura" if any(t.name == "cadena_verificar_factura" for t in tools) else tools[0].name
            return AIMessage("", tool_calls=[{"name": nombre, "args": {"id_negocio": "FAC-2026-10-0000003"}, "id": "c1"}])
        return AIMessage("según [1] ...")
    return responder


def armar(plan, accion=None):
    llamadas, registro = [], []
    llm = LLMFalso(guion(plan, accion), registro)
    buscar = lambda q, nic=None: [Fragmento("knowledge/politica-reclamos.md", "Política", "politica", None, "texto")]
    g = G.construir_grafo(llm, llm, herramientas(llamadas), buscar=buscar, checkpointer=MemorySaver())
    return g, llamadas, registro


def correr(g, entrada, hilo="t1"):
    return asyncio.run(g.ainvoke(entrada, {"configurable": {"thread_id": hilo}}))


MSG = {"messages": [HumanMessage("El cliente NIC-2001003 duda de su factura FAC-2026-10-0000003")]}


def test_solo_corren_los_especialistas_del_plan_y_se_extraen_los_ids():
    g, llamadas, _ = armar(["verificador", "conocimiento"])
    fin = correr(g, MSG)
    assert fin["nic"] == "NIC-2001003" and fin["factura"] == "FAC-2026-10-0000003"
    assert set(fin["hallazgos"]) == {"verificador", "conocimiento"}
    assert [n for n, _ in llamadas] == ["cadena_verificar_factura"]
    assert fin["hallazgos"]["conocimiento"]["fuentes"][0]["fuente"] == "knowledge/politica-reclamos.md"


def test_ningun_especialista_ve_herramientas_de_escritura():
    g, _, registro = armar(["cuenta_pagos", "consumo", "verificador"])
    correr(g, MSG)
    vistas = {n for tipo, nombres in registro if tipo == "tools" for n in nombres}
    assert vistas and not vistas & set(G.ESCRITURA)
    assert ["cadena_estado", "cadena_verificar_factura", "crm_facturas_de_cliente"] in [n for _, n in registro]


def test_plan_vacio_va_directo_al_sintetizador():
    g, llamadas, _ = armar([])
    fin = correr(g, {"messages": [HumanMessage("hola")]})
    assert llamadas == [] and fin["messages"][-1].content.startswith("resumen")


def test_la_escritura_se_detiene_hasta_aprobar_y_usa_datos_del_estado():
    accion = G.Accion(tipo="crear_reclamo", motivo="factura ALTERADA", prioridad="alta")
    g, llamadas, _ = armar(["verificador"], accion)
    fin = correr(g, MSG)
    assert "__interrupt__" in fin
    assert fin["__interrupt__"][0].value["accion"]["tipo"] == "crear_reclamo"
    assert not [n for n, _ in llamadas if n in G.ESCRITURA]

    fin = correr(g, Command(resume={"aprobado": True}))
    escrituras = [(n, kw) for n, kw in llamadas if n in G.ESCRITURA]
    assert escrituras == [("crm_crear_reclamo", {"nic": "NIC-2001003", "id_negocio": "FAC-2026-10-0000003",
                                                 "motivo": "factura ALTERADA", "prioridad": "alta"})]
    assert fin["resultado_accion"]["ejecutada"] is True


def test_rechazar_no_ejecuta_la_escritura():
    accion = G.Accion(tipo="crear_reclamo", motivo="x", prioridad="alta")
    g, llamadas, _ = armar(["verificador"], accion)
    correr(g, MSG, "t2")
    fin = correr(g, Command(resume={"aprobado": False}), "t2")
    assert not [n for n, _ in llamadas if n in G.ESCRITURA]
    assert fin["resultado_accion"] == {"ejecutada": False}


def test_registrar_pago_toma_monto_y_deudor_del_crm_no_del_modelo():
    accion = G.Accion(tipo="registrar_pago", motivo="el cliente quiere pagar")
    g, llamadas, _ = armar(["cuenta_pagos"], accion)
    correr(g, MSG, "t3")
    correr(g, Command(resume={"aprobado": True}), "t3")
    kw = next(kw for n, kw in llamadas if n == "pagos_registrar_pago")
    assert kw["deudor"] == "participant-wallet" and kw["monto_cop"] == 150000 and kw["id_negocio"] == "FAC-2026-10-0000003"


def test_sin_factura_ni_nic_no_hay_accion():
    accion = G.Accion(tipo="crear_reclamo", motivo="x")
    g, _, _ = armar([], accion)
    fin = correr(g, {"messages": [HumanMessage("abre un reclamo")]}, "t4")
    assert "__interrupt__" not in fin and fin["propuesta"] is None
