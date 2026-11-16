"""RAG híbrido: búsqueda densa (pgvector) + texto completo en español (tsvector), fusión RRF y
filtro por metadatos. Un fragmento con `nic` solo se devuelve al consultar por ese cliente."""
import os
import pathlib
import re
from dataclasses import dataclass
from functools import lru_cache

import psycopg
from psycopg.rows import dict_row

MODELO = os.environ.get("EMBEDDING_MODEL", "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2")
DB_RO = os.environ.get("COPILOTO_DB_RO", "postgresql://copiloto_ro:ro-demo@localhost:15432/copiloto")
K_RRF = 60
MAX_CARACTERES = 900


@dataclass
class Fragmento:
    fuente: str
    titulo: str
    tipo: str
    nic: str | None
    contenido: str
    puntaje: float = 0.0


@lru_cache(maxsize=1)
def _modelo():
    from fastembed import TextEmbedding

    return TextEmbedding(MODELO)


def embeber(textos: list[str]) -> list[list[float]]:
    return [v.tolist() for v in _modelo().embed(textos)]


# ---------------- Ingesta ----------------
def leer_documento(texto: str) -> tuple[dict, str]:
    """Separa el encabezado `--- clave: valor ---` del cuerpo."""
    meta = {}
    m = re.match(r"^---\n(.*?)\n---\n", texto, re.S)
    if m:
        for linea in m.group(1).splitlines():
            if ":" in linea:
                k, v = linea.split(":", 1)
                meta[k.strip()] = v.strip()
        texto = texto[m.end():]
    return meta, texto


def trocear(cuerpo: str, maximo: int = MAX_CARACTERES) -> list[str]:
    """Trocea por secciones `##`; una sección larga se parte por párrafos. Cada trozo conserva su título de sección."""
    trozos = []
    for seccion in re.split(r"\n(?=#{1,3} )", cuerpo.strip()):
        seccion = seccion.strip()
        if not seccion:
            continue
        if len(seccion) <= maximo:
            trozos.append(seccion)
            continue
        encabezado = seccion.splitlines()[0] if seccion.startswith("#") else ""
        actual = ""
        for parrafo in seccion.split("\n\n"):
            if actual and len(actual) + len(parrafo) > maximo:
                trozos.append(actual.strip())
                actual = encabezado + "\n\n" if encabezado else ""
            actual += parrafo + "\n\n"
        if actual.strip() and actual.strip() != encabezado:
            trozos.append(actual.strip())
    return [t for t in trozos if len(t) > 40]


def ingerir(documentos: list[tuple[str, str]], url_admin: str) -> int:
    """documentos: lista de (fuente, texto con encabezado). Reemplaza el índice completo."""
    filas = []
    for fuente, texto in documentos:
        meta, cuerpo = leer_documento(texto)
        for trozo in trocear(cuerpo):
            filas.append((fuente, meta.get("titulo", pathlib.Path(fuente).stem), meta.get("tipo", "tecnico"), meta.get("nic") or None, trozo))
    vectores = embeber([f"{f[1]}\n{f[4]}" for f in filas])
    with psycopg.connect(url_admin) as con:
        con.execute("TRUNCATE fragmentos")
        with con.cursor() as cur:
            cur.executemany(
                "INSERT INTO fragmentos(fuente,titulo,tipo,nic,contenido,embedding) VALUES (%s,%s,%s,%s,%s,%s::vector)",
                [(*f, str(v)) for f, v in zip(filas, vectores)],
            )
    return len(filas)


# ---------------- Recuperación ----------------
_VACIAS = set("a al algo como con cual cuando de del donde el en es esta este la las le lo los me mi no o para por que qué se si sin su sus un una y ya".split())


def consulta_tsquery(pregunta: str) -> str:
    """Términos unidos con OR: `plainto_tsquery` exige todos y casi nunca acierta con preguntas en lenguaje natural."""
    terminos = [t for t in re.findall(r"[\wáéíóúñü]+", pregunta.lower()) if len(t) > 2 and t not in _VACIAS]
    return " | ".join(dict.fromkeys(terminos))


SQL_HIBRIDO = """
WITH visibles AS (
  SELECT * FROM fragmentos WHERE (nic IS NULL OR nic = %(nic)s) AND (%(tipo)s::text IS NULL OR tipo = %(tipo)s)
), denso AS (
  SELECT id, row_number() OVER (ORDER BY embedding <=> %(vec)s::vector) AS pos FROM visibles ORDER BY embedding <=> %(vec)s::vector LIMIT 20
), lexico AS (
  SELECT id, row_number() OVER (ORDER BY ts_rank_cd(tsv, q) DESC) AS pos
  FROM visibles, to_tsquery('spanish', %(tsq)s) q WHERE %(tsq)s <> '' AND tsv @@ q ORDER BY ts_rank_cd(tsv, q) DESC LIMIT 20
)
SELECT f.fuente, f.titulo, f.tipo, f.nic, f.contenido,
       COALESCE(1.0/(%(k)s + d.pos), 0) + COALESCE(1.0/(%(k)s + l.pos), 0) AS puntaje
FROM visibles f LEFT JOIN denso d USING (id) LEFT JOIN lexico l USING (id)
WHERE d.id IS NOT NULL OR l.id IS NOT NULL
ORDER BY puntaje DESC LIMIT %(top)s
"""


def buscar(pregunta: str, nic: str | None = None, tipo: str | None = None, top: int = 5, url: str | None = None) -> list[Fragmento]:
    vec = str(embeber([pregunta])[0])
    with psycopg.connect(url or DB_RO, row_factory=dict_row) as con:
        filas = con.execute(SQL_HIBRIDO, {"nic": nic, "tipo": tipo, "vec": vec, "tsq": consulta_tsquery(pregunta), "k": K_RRF, "top": top}).fetchall()
    return [Fragmento(**{**f, "puntaje": float(f["puntaje"])}) for f in filas]


def formatear(fragmentos: list[Fragmento]) -> str:
    return "\n\n".join(f"[{i}] ({f.fuente} · {f.titulo})\n{f.contenido}" for i, f in enumerate(fragmentos, 1))
