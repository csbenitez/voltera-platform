"""Único punto donde se construye el LLM. El proveedor se cambia por variables de entorno, sin tocar el grafo.

| LLM_PROVIDER  | Llave              | Notas                                                             |
|---------------|--------------------|-------------------------------------------------------------------|
| google_genai  | GOOGLE_API_KEY     | Por defecto. Nivel gratuito de Gemini (AI Studio).                |
| groq          | GROQ_API_KEY       | Nivel gratuito, modelos abiertos.                                 |
| openai        | OPENAI_API_KEY     | Con OPENAI_BASE_URL sirve para GitHub Models y Azure AI Foundry.  |
| anthropic     | ANTHROPIC_API_KEY  | Claude.                                                           |

Los niveles gratuitos limitan las peticiones por minuto: LLM_RPS fija el ritmo y se reintenta ante 429.
"""
import os
import pathlib

from langchain.chat_models import init_chat_model
from langchain_core.rate_limiters import InMemoryRateLimiter

_limitador = None


def _cargar_env():
    """Fuera de Docker, toma la configuración del .env de la raíz del monorepo (sin pisar variables ya definidas)."""
    ruta = pathlib.Path(__file__).resolve().parents[3] / ".env"
    if not ruta.exists():
        return
    for linea in ruta.read_text(encoding="utf-8").splitlines():
        if "=" in linea and not linea.lstrip().startswith("#"):
            k, v = linea.split("=", 1)
            if v.strip():
                os.environ.setdefault(k.strip(), v.strip())


_cargar_env()


def _ritmo():
    global _limitador
    if _limitador is None:
        _limitador = InMemoryRateLimiter(requests_per_second=float(os.environ.get("LLM_RPS", "0.2")), max_bucket_size=3)
    return _limitador


def crear_llm(rol: str = "rapido"):
    """rol: 'fuerte' (supervisor y sintetizador) o 'rapido' (especialistas)."""
    proveedor = os.environ.get("LLM_PROVIDER", "google_genai")
    modelo = os.environ.get("LLM_MODEL") or ("gemini-2.5-flash" if proveedor == "google_genai" else "")
    if rol == "fuerte":
        modelo = os.environ.get("LLM_MODEL_FUERTE") or modelo
    if not modelo:
        raise RuntimeError("Falta LLM_MODEL en .env (en Azure Foundry es el nombre del despliegue del modelo).")
    return init_chat_model(modelo, model_provider=proveedor, temperature=0, rate_limiter=_ritmo(), max_retries=4)
