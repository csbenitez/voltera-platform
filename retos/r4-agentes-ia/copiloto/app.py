"""Interfaz del asesor (Chainlit). Uso: chainlit run copiloto/app.py --port 8501
Muestra qué especialista trabajó y con qué herramientas, y pide aprobación antes de cualquier escritura."""
import uuid

import chainlit as cl
from langchain_core.messages import HumanMessage
from langgraph.types import Command

from copiloto.grafo import ESPECIALISTAS, texto
from copiloto.runtime import crear_copiloto

ORIGEN = {"conocimiento": "RAG", "cuenta_pagos": "R1 service mesh + BD", "consumo": "R2 event mesh", "verificador": "R3 blockchain"}


@cl.on_chat_start
async def inicio():
    cl.user_session.set("grafo", await crear_copiloto())
    cl.user_session.set("cfg", {"configurable": {"thread_id": uuid.uuid4().hex}})
    await cl.Message("Copiloto de facturación listo. Indica el NIC del cliente y su consulta, por ejemplo: "
                     "«El cliente NIC-2001001 dice que su factura llegó muy alta».").send()


async def ejecutar(entrada):
    """Corre el grafo mostrando cada nodo. Devuelve la acción pendiente de aprobación, si la hay."""
    grafo, cfg = cl.user_session.get("grafo"), cl.user_session.get("cfg")
    pendiente = None
    async for paso in grafo.astream(entrada, cfg, stream_mode="updates"):
        for nodo, cambio in paso.items():
            if nodo == "__interrupt__":
                pendiente = cambio[0].value["accion"]
            elif nodo == "supervisor":
                async with cl.Step(name="supervisor") as s:
                    s.output = f"Especialistas: {', '.join(cambio['plan']) or 'ninguno'} · NIC: {cambio.get('nic') or '—'} · Factura: {cambio.get('factura') or '—'}"
            elif nodo in ESPECIALISTAS:
                h = cambio["hallazgos"][nodo]
                usados = [f"{e['herramienta']}({e['args']})" for e in h.get("evidencia", [])] or [f"[{f['n']}] {f['fuente']}" for f in h.get("fuentes", [])]
                async with cl.Step(name=f"{nodo} · {ORIGEN[nodo]}") as s:
                    s.output = h["conclusion"] + "\n\n" + "\n".join(f"- {u}" for u in usados)
            elif cambio and cambio.get("messages"):
                await cl.Message(texto(cambio["messages"][-1])).send()
    return pendiente


@cl.on_message
async def mensaje(m: cl.Message):
    pendiente = await ejecutar({"messages": [HumanMessage(m.content)]})
    while pendiente:
        r = await cl.AskActionMessage(
            content=f"¿Apruebas **{pendiente['tipo']}** sobre {pendiente['factura']} (NIC {pendiente['nic']})?\nMotivo: {pendiente['motivo']}",
            actions=[cl.Action(name="aprobar", payload={"aprobado": True}, label="Aprobar"),
                     cl.Action(name="rechazar", payload={"aprobado": False}, label="Rechazar")],
            timeout=300,
        ).send()
        aprobado = bool(r and r.get("payload", {}).get("aprobado"))
        pendiente = await ejecutar(Command(resume={"aprobado": aprobado}))
