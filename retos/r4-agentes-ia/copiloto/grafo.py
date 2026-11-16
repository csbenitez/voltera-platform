"""Grafo multiagente del copiloto de facturación.

supervisor -> (especialistas en paralelo) -> sintetizador -> [interrupt: aprobación humana] -> acciones

Cada especialista consume una arquitectura distinta y solo ve sus herramientas:
  conocimiento  -> RAG híbrido (políticas, tarifas, contrato del cliente actual)
  cuenta_pagos  -> MCP crm_* y pagos_*   (R1 service mesh + base de datos)
  consumo       -> MCP eventos_*         (R2 event mesh)
  verificador   -> MCP cadena_*          (R3 blockchain)
Las herramientas de escritura solo las ejecuta el nodo `acciones`, y solo tras aprobar.
"""
import asyncio
import json
import re
import uuid
from typing import Annotated, Literal, TypedDict

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.types import Send, interrupt
from pydantic import BaseModel, Field

from . import rag

ESPECIALISTAS = ("conocimiento", "cuenta_pagos", "consumo", "verificador")
LECTURA = {
    "cuenta_pagos": ["crm_buscar_cliente", "crm_facturas_de_cliente", "crm_reclamos_de_cliente",
                     "pagos_participante", "pagos_por_factura", "pagos_consultar_pago"],
    "consumo": ["crm_buscar_cliente", "eventos_calces_por_medidor", "eventos_estadisticas"],
    "verificador": ["crm_facturas_de_cliente", "cadena_verificar_factura", "cadena_estado"],
}
ESCRITURA = ("crm_crear_reclamo", "pagos_registrar_pago")
ACREEDOR = "participant-bank"  # participante que recauda las facturas en la demo
RE_NIC = re.compile(r"\bNIC-\d{7}\b")
RE_FAC = re.compile(r"\bFAC-\d{4}-\d{2}-\d{7}\b")
MAX_PASOS = 4


def _unir(a: dict, b: dict) -> dict:
    return {**(a or {}), **(b or {})}


class Estado(TypedDict, total=False):
    messages: Annotated[list, add_messages]
    nic: str | None
    factura: str | None
    pregunta: str
    plan: list[str]
    hallazgos: Annotated[dict, _unir]
    propuesta: dict | None
    resultado_accion: dict | None


class Plan(BaseModel):
    """Qué especialistas deben trabajar para responder al asesor."""
    especialistas: list[Literal["conocimiento", "cuenta_pagos", "consumo", "verificador"]] = Field(
        description="Solo los necesarios. Vacío si es un saludo o no requiere datos.")
    pregunta: str = Field(description="La necesidad del asesor reformulada en una frase autocontenida.")


class Accion(BaseModel):
    tipo: Literal["crear_reclamo", "registrar_pago"]
    motivo: str = Field(description="Una frase que justifica la acción, basada en los hallazgos.")
    prioridad: Literal["normal", "alta"] = "normal"


class Sintesis(BaseModel):
    respuesta: str = Field(description="Explicación para el asesor, con los datos y la evidencia que la sustentan.")
    guion_cliente: str = Field(description="Lo que el asesor puede decirle al cliente, en lenguaje sencillo.")
    accion: Accion | None = Field(default=None, description="Solo si los hallazgos y la política la justifican.")


class Suficiencia(BaseModel):
    suficiente: bool = Field(description="True si los fragmentos bastan para responder.")
    consulta_reformulada: str = Field(default="", description="Si no bastan, una consulta mejor.")


def texto(msg) -> str:
    c = getattr(msg, "content", msg)
    if isinstance(c, str):
        return c
    return "".join(p.get("text", "") if isinstance(p, dict) else str(p) for p in c)


def _json(v) -> str:
    if isinstance(v, str):
        return v
    if isinstance(v, list) and v and isinstance(v[0], dict) and "text" in v[0]:
        return "".join(p.get("text", "") for p in v)
    return json.dumps(v, ensure_ascii=False, default=str)


async def bucle_herramientas(llm, tools: list, sistema: str, tarea: str) -> tuple[str, list[dict]]:
    """Bucle mínimo de llamada a herramientas. Devuelve la conclusión y la evidencia cruda de cada llamada."""
    por_nombre = {t.name: t for t in tools}
    modelo = llm.bind_tools(tools)
    mensajes = [SystemMessage(sistema), HumanMessage(tarea)]
    evidencia = []
    for _ in range(MAX_PASOS):
        r = await modelo.ainvoke(mensajes)
        mensajes.append(r)
        if not getattr(r, "tool_calls", None):
            return texto(r), evidencia
        for tc in r.tool_calls:
            t = por_nombre.get(tc["name"])
            try:
                salida = _json(await t.ainvoke(tc["args"])) if t else f"herramienta no disponible: {tc['name']}"
            except Exception as e:  # el error vuelve al modelo como dato
                salida = f"error: {e}"
            evidencia.append({"herramienta": tc["name"], "args": tc["args"], "resultado": salida[:4000]})
            mensajes.append(ToolMessage(content=salida[:4000], tool_call_id=tc["id"]))
    return "No se alcanzó una conclusión con los pasos permitidos.", evidencia


SIS_SUPERVISOR = """Eres el supervisor de un copiloto para asesores del call center de facturación de Voltera (energía P2P).
Decide qué especialistas deben trabajar. Elige solo los necesarios:
- conocimiento: políticas, tarifas, procedimientos, preguntas frecuentes y el contrato del cliente.
- cuenta_pagos: datos del cliente, sus facturas, estado de pagos y reclamos.
- consumo: calces de energía P2P del medidor (explican compra_p2p y venta_p2p).
- verificador: integridad de una factura contra la cadena de bloques.
Si falta el NIC o la factura y son necesarios, incluye igualmente a cuenta_pagos para buscarlos."""

SIS_ESPECIALISTA = {
    "cuenta_pagos": "Eres el especialista de cuenta y pagos. Usa las herramientas para obtener datos del cliente, sus facturas "
                    "y los pagos asociados (la referencia de un pago es el id de la factura; amount_minor está en centavos). "
                    "Compara el estado de cobro de la factura con los pagos. Reporta solo hechos obtenidos de las herramientas.",
    "consumo": "Eres el especialista de consumo. Obtén el medidor del cliente y consulta sus calces de energía P2P. "
               "Resume cuántos calces hubo, kWh comprados y vendidos y precios. Reporta solo hechos de las herramientas.",
    "verificador": "Eres el especialista de integridad. Verifica la factura contra la cadena y reporta el veredicto exacto, "
                   "el motivo y la evidencia (huellas y transacción). Si el veredicto es SIN_VEREDICTO, dilo tal cual: "
                   "nunca lo conviertas en ALTERADA ni en ÍNTEGRA.",
}

SIS_SINTESIS = """Eres el copiloto de un asesor del call center de facturación de Voltera. Con los hallazgos de los especialistas:
1. Responde al asesor de forma breve y concreta, citando cifras, veredictos y fuentes ([n] de la base de conocimiento).
2. Redacta un guion corto para el cliente, sin jerga.
3. Propón una acción SOLO si la política lo indica: crear_reclamo (prioridad alta si la factura está ALTERADA o hay un pago
   aprobado con la factura aún pendiente) o registrar_pago (si el cliente quiere pagar y no hay pago aprobado).
No inventes datos. Si falta el NIC o la factura, pídelos. Si un especialista no obtuvo datos, dilo.
Un veredicto SIN_VEREDICTO significa que la cadena no respondió: no concluyas nada sobre la integridad."""


def construir_grafo(llm_fuerte, llm_rapido, tools: list, buscar=rag.buscar, checkpointer=None):
    por_nombre = {t.name: t for t in tools}

    def subconjunto(nombres):
        return [por_nombre[n] for n in nombres if n in por_nombre]

    def contexto(e: Estado) -> str:
        return f"NIC del cliente: {e.get('nic') or 'desconocido'}. Factura en foco: {e.get('factura') or 'no indicada'}."

    async def supervisor(e: Estado):
        ultimo = texto(e["messages"][-1])
        nic = (RE_NIC.findall(ultimo) or [e.get("nic")])[-1]
        fac = (RE_FAC.findall(ultimo) or [e.get("factura")])[-1]
        historial = "\n".join(f"{m.type}: {texto(m)[:500]}" for m in e["messages"][-6:])
        plan = await llm_fuerte.with_structured_output(Plan).ainvoke(
            [SystemMessage(SIS_SUPERVISOR), HumanMessage(f"NIC: {nic or 'desconocido'}. Factura: {fac or 'no indicada'}.\n{historial}")])
        elegidos = [x for x in dict.fromkeys(plan.especialistas) if x in ESPECIALISTAS]
        return {"nic": nic, "factura": fac, "plan": elegidos, "pregunta": plan.pregunta or ultimo,
                "hallazgos": {}, "propuesta": None, "resultado_accion": None}

    def repartir(e: Estado):
        return [Send(n, e) for n in e["plan"]] or "sintetizador"

    def especialista(nombre: str):
        async def nodo(e: Estado):
            conclusion, evidencia = await bucle_herramientas(
                llm_rapido, subconjunto(LECTURA[nombre]), SIS_ESPECIALISTA[nombre], f"{contexto(e)}\nNecesidad: {e['pregunta']}")
            return {"hallazgos": {nombre: {"conclusion": conclusion, "evidencia": evidencia}}}
        return nodo

    async def conocimiento(e: Estado):
        """RAG agéntico: recupera, califica y, si no alcanza, reformula una vez."""
        pregunta = e["pregunta"]
        frags = await asyncio.to_thread(buscar, pregunta, e.get("nic"))
        juicio = await llm_rapido.with_structured_output(Suficiencia).ainvoke(
            [SystemMessage("Califica si los fragmentos bastan para responder la pregunta. Si no, propone una consulta mejor."),
             HumanMessage(f"Pregunta: {pregunta}\n\nFragmentos:\n{rag.formatear(frags)}")])
        if not juicio.suficiente and juicio.consulta_reformulada:
            extra = await asyncio.to_thread(buscar, juicio.consulta_reformulada, e.get("nic"))
            vistos = {f.contenido for f in frags}
            frags += [f for f in extra if f.contenido not in vistos]
        r = await llm_rapido.ainvoke(
            [SystemMessage("Responde solo con lo que dicen los fragmentos y cita cada afirmación con su número [n]. "
                           "Si no está en los fragmentos, dilo."),
             HumanMessage(f"{contexto(e)}\nPregunta: {pregunta}\n\nFragmentos:\n{rag.formatear(frags)}")])
        fuentes = [{"n": i, "fuente": f.fuente, "titulo": f.titulo} for i, f in enumerate(frags, 1)]
        return {"hallazgos": {"conocimiento": {"conclusion": texto(r), "fuentes": fuentes}}}

    async def sintetizador(e: Estado):
        hallazgos = json.dumps(e.get("hallazgos") or {}, ensure_ascii=False, default=str)[:14000]
        s = await llm_fuerte.with_structured_output(Sintesis).ainvoke(
            [SystemMessage(SIS_SINTESIS),
             HumanMessage(f"{contexto(e)}\nPregunta del asesor: {e.get('pregunta')}\n\nHallazgos:\n{hallazgos}")])
        # Si el asesor no nombró la factura pero los hallazgos hablan de una sola, esa es la factura en foco.
        factura = e.get("factura")
        if not factura:
            unicas = set(RE_FAC.findall(hallazgos))
            factura = unicas.pop() if len(unicas) == 1 else None
        propuesta = None
        if s.accion and e.get("nic") and factura:
            propuesta = {"tipo": s.accion.tipo, "motivo": s.accion.motivo, "prioridad": s.accion.prioridad,
                         "nic": e["nic"], "factura": factura}
        cuerpo = f"{s.respuesta}\n\n**Guion para el cliente:** {s.guion_cliente}"
        if propuesta:
            cuerpo += f"\n\n**Acción propuesta (requiere tu aprobación):** {propuesta['tipo']} · {propuesta['motivo']}"
        return {"messages": [AIMessage(cuerpo)], "propuesta": propuesta, "factura": factura}

    def tras_sintesis(e: Estado):
        return "acciones" if e.get("propuesta") else END

    async def acciones(e: Estado):
        p = e["propuesta"]
        decision = interrupt({"accion": p, "pregunta": "¿Apruebas ejecutar esta acción?"})
        if not (isinstance(decision, dict) and decision.get("aprobado") is True):
            return {"messages": [AIMessage("Acción no ejecutada: el asesor no la aprobó.")], "resultado_accion": {"ejecutada": False}}
        if p["tipo"] == "crear_reclamo":
            args = {"nic": p["nic"], "id_negocio": p["factura"], "motivo": p["motivo"][:500], "prioridad": p["prioridad"]}
            salida = await por_nombre["crm_crear_reclamo"].ainvoke(args)
        else:
            # Los datos del pago salen del CRM, no del modelo: participante del cliente y total de la factura.
            cliente = json.loads(_json(await por_nombre["crm_buscar_cliente"].ainvoke({"nic": p["nic"]})))
            facturas = json.loads(_json(await por_nombre["crm_facturas_de_cliente"].ainvoke({"nic": p["nic"]})))
            facturas = facturas if isinstance(facturas, list) else [facturas]
            fac = next((f for f in facturas if f.get("id_negocio") == p["factura"]), None)
            if not fac or "participant_id" not in cliente:
                return {"messages": [AIMessage("No se pudo registrar el pago: la factura no pertenece a este cliente.")],
                        "resultado_accion": {"ejecutada": False}}
            args = {"id_negocio": p["factura"], "deudor": cliente["participant_id"], "acreedor": ACREEDOR,
                    "monto_cop": int(fac["cop"]), "clave_idempotencia": f"copiloto-{p['factura']}-{uuid.uuid4().hex[:8]}"}
            salida = await por_nombre["pagos_registrar_pago"].ainvoke(args)
        return {"messages": [AIMessage(f"Acción ejecutada ({p['tipo']}): {_json(salida)[:600]}")],
                "resultado_accion": {"ejecutada": True, "args": args}}

    g = StateGraph(Estado)
    g.add_node("supervisor", supervisor)
    g.add_node("conocimiento", conocimiento)
    for n in LECTURA:
        g.add_node(n, especialista(n))
    g.add_node("sintetizador", sintetizador)
    g.add_node("acciones", acciones)
    g.add_edge(START, "supervisor")
    g.add_conditional_edges("supervisor", repartir, [*ESPECIALISTAS, "sintetizador"])
    for n in ESPECIALISTAS:
        g.add_edge(n, "sintetizador")
    g.add_conditional_edges("sintetizador", tras_sintesis, ["acciones", END])
    g.add_edge("acciones", END)
    return g.compile(checkpointer=checkpointer)
