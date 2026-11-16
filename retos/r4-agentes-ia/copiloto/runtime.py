"""Ensambla el copiloto real: herramientas del servidor MCP, LLM configurado y grafo con memoria de conversación."""
import os

from langchain_mcp_adapters.client import MultiServerMCPClient
from langgraph.checkpoint.memory import MemorySaver

from .grafo import construir_grafo
from .llm import crear_llm

MCP_URL = os.environ.get("MCP_URL", "http://localhost:18000/mcp")


async def crear_copiloto(checkpointer=None):
    cliente = MultiServerMCPClient({"voltera": {"url": MCP_URL, "transport": "streamable_http"}})
    tools = await cliente.get_tools()
    return construir_grafo(crear_llm("fuerte"), crear_llm("rapido"), tools, checkpointer=checkpointer or MemorySaver())


def pendiente_de_aprobacion(resultado: dict):
    """Devuelve la acción en espera si el grafo se detuvo en la aprobación humana."""
    cortes = resultado.get("__interrupt__") or []
    return cortes[0].value["accion"] if cortes else None
