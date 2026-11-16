"""Construye el índice del RAG: base de conocimiento + un contrato por cliente + documentos técnicos de los retos.
Uso: python -m copiloto.ingesta   (variable COPILOTO_DB_ADMIN)"""
import json
import os
import pathlib

from . import rag

R4 = pathlib.Path(__file__).resolve().parent.parent
RAIZ = R4.parent.parent
ADMIN = os.environ.get("COPILOTO_DB_ADMIN", "postgresql://postgres:postgres@localhost:15432/copiloto")

# Documentos ya existentes en los otros retos que sirven al asesor.
TECNICOS = [
    ("retos/r3-blockchain/docs/voltera/especificacion-huella.md", "Especificación de la huella de una factura"),
    ("retos/r3-blockchain/docs/voltera/consumo-por-roles.md", "Contrato de registro de facturas y servicio de verificación"),
    ("retos/r2-event-mesh/docs/EVENT_MESH.md", "Event mesh: enrutamiento, deduplicación y failover"),
]


def documentos() -> list[tuple[str, str]]:
    docs = []
    for p in sorted((R4 / "knowledge").glob("*.md")):
        if p.name != "contrato.plantilla.md":
            docs.append((f"knowledge/{p.name}", p.read_text(encoding="utf-8")))
    plantilla = (R4 / "knowledge" / "contrato.plantilla.md").read_text(encoding="utf-8")
    for c in json.loads((RAIZ / "seed" / "clientes.json").read_text(encoding="utf-8")):
        docs.append((f"contratos/{c['nic']}.md", plantilla.format(**c)))
    for ruta, titulo in TECNICOS:
        p = RAIZ / ruta
        if p.exists():
            docs.append((ruta, f"---\ntitulo: {titulo}\ntipo: tecnico\n---\n{p.read_text(encoding='utf-8')}"))
    return docs


if __name__ == "__main__":
    docs = documentos()
    n = rag.ingerir(docs, ADMIN)
    print(f"Indexados {n} fragmentos de {len(docs)} documentos")
