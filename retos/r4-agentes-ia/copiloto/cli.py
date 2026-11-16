"""Copiloto por consola. Uso: python -m copiloto.cli "El cliente NIC-2001003 duda de su factura"
Sin argumento abre una conversación interactiva. Las acciones de escritura piden confirmación."""
import asyncio
import sys

from langchain_core.messages import HumanMessage
from langgraph.types import Command

from .grafo import texto
from .runtime import crear_copiloto, pendiente_de_aprobacion


async def turno(grafo, entrada, cfg):
    resultado = await grafo.ainvoke(entrada, cfg)
    while (accion := pendiente_de_aprobacion(resultado)) is not None:
        print(f"\n{texto(resultado['messages'][-1])}\n")
        ok = input(f"¿Aprobar {accion['tipo']} sobre {accion['factura']}? [s/N] ").strip().lower() == "s"
        resultado = await grafo.ainvoke(Command(resume={"aprobado": ok}), cfg)
    print(f"\n{texto(resultado['messages'][-1])}\n")
    for nombre, h in (resultado.get("hallazgos") or {}).items():
        usados = ", ".join(e["herramienta"] for e in h.get("evidencia", [])) or ", ".join(f["fuente"] for f in h.get("fuentes", []))
        print(f"  · {nombre}: {usados}")


async def main():
    grafo = await crear_copiloto()
    cfg = {"configurable": {"thread_id": "cli"}}
    if len(sys.argv) > 1:
        await turno(grafo, {"messages": [HumanMessage(" ".join(sys.argv[1:]))]}, cfg)
        return
    while (linea := input("asesor> ").strip()) not in ("", "salir"):
        await turno(grafo, {"messages": [HumanMessage(linea)]}, cfg)


if __name__ == "__main__":
    asyncio.run(main())
