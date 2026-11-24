"""
Experimento E5 / IA-E1 - Benchmark de recuperación RAG de Voltera.

Compara sobre el mismo conjunto dorado:

A. Recuperación vectorial con pgvector
B. Recuperación léxica con PostgreSQL tsvector
C. Recuperación híbrida con Reciprocal Rank Fusion (RRF)

Además verifica aislamiento multi-tenant por NIC.

Hipótesis H1
-------------
La recuperación híbrida obtiene Hit@3 y MRR iguales o superiores a
las estrategias individuales, manteniendo p95 total < 200 ms.

Hipótesis H2
-------------
El prefiltrado SQL por NIC evita cualquier fuga de documentos
contractuales entre clientes.

La ejecución genera:
  docs/evidencias/experimento-e5-resultados.json
  docs/evidencias/experimento-e5-detalle.csv
"""

from __future__ import annotations

import argparse
import csv
import json
import statistics
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import psycopg
from psycopg.rows import dict_row


# ---------------------------------------------------------------------
# Localización del proyecto
# ---------------------------------------------------------------------

RAIZ = Path(__file__).resolve().parents[1]
R4 = RAIZ / "retos" / "r4-agentes-ia"

sys.path.insert(0, str(R4))

from copiloto import rag  # noqa: E402


# ---------------------------------------------------------------------
# Dataset dorado
# Una fuente relevante conocida por pregunta.
# ---------------------------------------------------------------------

DORADO = [
    (
        "¿Cuánto cuesta el kWh comprado a un vecino?",
        None,
        "knowledge/tarifas-2026-10.md",
    ),
    (
        "¿Por qué le subió la factura este mes al cliente?",
        None,
        "knowledge/tarifas-2026-10.md",
    ),
    (
        "¿Qué significa la línea excedente con valor negativo?",
        None,
        "knowledge/tarifas-2026-10.md",
    ),
    (
        "El cliente ya pagó y la factura sigue pendiente, ¿qué hago?",
        None,
        "knowledge/faq-pagos.md",
    ),
    (
        "¿Se cobra dos veces si se reintenta el pago?",
        None,
        "knowledge/faq-pagos.md",
    ),
    (
        "¿Cuándo vence la factura?",
        None,
        "knowledge/faq-pagos.md",
    ),
    (
        "La verificación dio ALTERADA, ¿qué prioridad tiene el reclamo?",
        None,
        "knowledge/politica-reclamos.md",
    ),
    (
        "¿En cuántos días se responde un reclamo de prioridad normal?",
        None,
        "knowledge/politica-reclamos.md",
    ),
    (
        "La factura aparece como NO ANCLADA, ¿está mal?",
        None,
        "knowledge/politica-reclamos.md",
    ),
    (
        "¿Qué evidencia de la cadena le entrego al cliente?",
        None,
        "knowledge/procedimiento-verificacion.md",
    ),
    (
        "¿Qué quiere decir el motivo huella_distinta?",
        None,
        "knowledge/procedimiento-verificacion.md",
    ),
    (
        "¿Cómo se convierten los calces de energía en la línea compra_p2p?",
        None,
        "knowledge/energia-p2p.md",
    ),
    (
        "¿A qué precio se cobra un calce de energía?",
        None,
        "knowledge/energia-p2p.md",
    ),
    (
        "¿Qué capacidad de paneles tiene instalada el titular?",
        "NIC-2001001",
        "contratos/NIC-2001001.md",
    ),
    (
        "¿Con qué medio de pago paga el titular?",
        "NIC-2001002",
        "contratos/NIC-2001002.md",
    ),
]


# ---------------------------------------------------------------------
# SQL de las estrategias
# ---------------------------------------------------------------------

SQL_VECTORIAL = """
WITH visibles AS (
    SELECT *
    FROM fragmentos
    WHERE (nic IS NULL OR nic = %(nic)s)
)
SELECT
    fuente,
    titulo,
    tipo,
    nic,
    contenido,
    1.0 - (embedding <=> %(vec)s::vector) AS puntaje
FROM visibles
ORDER BY embedding <=> %(vec)s::vector
LIMIT %(top)s
"""


SQL_LEXICO = """
WITH visibles AS (
    SELECT *
    FROM fragmentos
    WHERE (nic IS NULL OR nic = %(nic)s)
),
q AS (
    SELECT to_tsquery('spanish', %(tsq)s) AS consulta
)
SELECT
    v.fuente,
    v.titulo,
    v.tipo,
    v.nic,
    v.contenido,
    ts_rank_cd(v.tsv, q.consulta) AS puntaje
FROM visibles v, q
WHERE %(tsq)s <> ''
  AND v.tsv @@ q.consulta
ORDER BY puntaje DESC
LIMIT %(top)s
"""


# ---------------------------------------------------------------------
# Utilidades
# ---------------------------------------------------------------------

def percentil95(valores: list[float]) -> float:
    if not valores:
        return 0.0

    if len(valores) == 1:
        return valores[0]

    return statistics.quantiles(
        valores,
        n=100,
        method="inclusive",
    )[94]


def commit_actual() -> str:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            cwd=RAIZ,
            text=True,
        ).strip()
    except Exception:
        return "desconocido"


def rank_fuente(fuentes: list[str], esperada: str) -> int | None:
    try:
        return fuentes.index(esperada) + 1
    except ValueError:
        return None


# ---------------------------------------------------------------------
# Recuperadores
# ---------------------------------------------------------------------

def ejecutar_vectorial(
    con,
    pregunta: str,
    nic: str | None,
    top: int,
):
    inicio_total = time.perf_counter()

    inicio_embedding = time.perf_counter()
    vector = str(rag.embeber([pregunta])[0])
    embedding_ms = (time.perf_counter() - inicio_embedding) * 1000

    inicio_sql = time.perf_counter()
    filas = con.execute(
        SQL_VECTORIAL,
        {
            "nic": nic,
            "vec": vector,
            "top": top,
        },
    ).fetchall()
    sql_ms = (time.perf_counter() - inicio_sql) * 1000

    total_ms = (time.perf_counter() - inicio_total) * 1000

    return filas, embedding_ms, sql_ms, total_ms


def ejecutar_lexico(
    con,
    pregunta: str,
    nic: str | None,
    top: int,
):
    inicio_total = time.perf_counter()

    tsq = rag.consulta_tsquery(pregunta)

    inicio_sql = time.perf_counter()
    filas = con.execute(
        SQL_LEXICO,
        {
            "nic": nic,
            "tsq": tsq,
            "top": top,
        },
    ).fetchall()
    sql_ms = (time.perf_counter() - inicio_sql) * 1000

    total_ms = (time.perf_counter() - inicio_total) * 1000

    return filas, 0.0, sql_ms, total_ms


def ejecutar_hibrido(
    con,
    pregunta: str,
    nic: str | None,
    top: int,
):
    inicio_total = time.perf_counter()

    inicio_embedding = time.perf_counter()
    vector = str(rag.embeber([pregunta])[0])
    embedding_ms = (time.perf_counter() - inicio_embedding) * 1000

    inicio_sql = time.perf_counter()
    filas = con.execute(
        rag.SQL_HIBRIDO,
        {
            "nic": nic,
            "tipo": None,
            "vec": vector,
            "tsq": rag.consulta_tsquery(pregunta),
            "k": rag.K_RRF,
            "top": top,
        },
    ).fetchall()
    sql_ms = (time.perf_counter() - inicio_sql) * 1000

    total_ms = (time.perf_counter() - inicio_total) * 1000

    return filas, embedding_ms, sql_ms, total_ms


ESTRATEGIAS = {
    "vectorial": ejecutar_vectorial,
    "lexico": ejecutar_lexico,
    "hibrido_rrf": ejecutar_hibrido,
}


# ---------------------------------------------------------------------
# Benchmark
# ---------------------------------------------------------------------

def evaluar_estrategia(
    nombre: str,
    funcion,
    con,
    repeticiones: int,
    top: int,
):
    ranks = []

    latencias_embedding = []
    latencias_sql = []
    latencias_total = []

    detalle = []

    for indice, (pregunta, nic, esperada) in enumerate(DORADO, start=1):

        ranking_referencia = None

        for repeticion in range(1, repeticiones + 1):

            filas, embed_ms, sql_ms, total_ms = funcion(
                con,
                pregunta,
                nic,
                top,
            )

            fuentes = [f["fuente"] for f in filas]

            rank = rank_fuente(fuentes, esperada)

            if ranking_referencia is None:
                ranking_referencia = rank

            latencias_embedding.append(embed_ms)
            latencias_sql.append(sql_ms)
            latencias_total.append(total_ms)

            detalle.append(
                {
                    "estrategia": nombre,
                    "pregunta_id": indice,
                    "repeticion": repeticion,
                    "pregunta": pregunta,
                    "nic": nic,
                    "fuente_esperada": esperada,
                    "rank": rank,
                    "hit_1": rank == 1,
                    "hit_3": rank is not None and rank <= 3,
                    "fuentes_recuperadas": fuentes,
                    "embedding_ms": round(embed_ms, 3),
                    "sql_ms": round(sql_ms, 3),
                    "total_ms": round(total_ms, 3),
                }
            )

        ranks.append(ranking_referencia)

    n = len(DORADO)

    hit1 = sum(r == 1 for r in ranks)
    hit3 = sum(r is not None and r <= 3 for r in ranks)

    reciprocos = [
        1.0 / r if r is not None and r <= 3 else 0.0
        for r in ranks
    ]

    return {
        "estrategia": nombre,
        "preguntas": n,

        "hit_at_1": {
            "aciertos": hit1,
            "total": n,
            "porcentaje": round(hit1 / n * 100, 2),
        },

        "hit_at_3": {
            "aciertos": hit3,
            "total": n,
            "porcentaje": round(hit3 / n * 100, 2),
        },

        "mrr_at_3": round(
            sum(reciprocos) / n,
            4,
        ),

        "latencia_embedding_ms": {
            "mediana": round(
                statistics.median(latencias_embedding),
                3,
            ),
            "p95": round(
                percentil95(latencias_embedding),
                3,
            ),
        },

        "latencia_sql_ms": {
            "mediana": round(
                statistics.median(latencias_sql),
                3,
            ),
            "p95": round(
                percentil95(latencias_sql),
                3,
            ),
        },

        "latencia_total_ms": {
            "mediana": round(
                statistics.median(latencias_total),
                3,
            ),
            "p95": round(
                percentil95(latencias_total),
                3,
            ),
        },

        "ranks": ranks,

    }, detalle


# ---------------------------------------------------------------------
# Aislamiento por NIC
# ---------------------------------------------------------------------

def evaluar_aislamiento(con):
    consultas = [
        "contrato del titular plan paneles medio de pago",
        "NIC-2001002 José Ángel Núñez contrato",
    ]

    nics = [
        None,
        "NIC-2001001",
        "NIC-2001003",
    ]

    escenarios = []
    fugas = 0

    for nic in nics:
        for pregunta in consultas:

            filas, _, _, _ = ejecutar_hibrido(
                con,
                pregunta,
                nic,
                20,
            )

            filtraciones = [
                {
                    "fuente": f["fuente"],
                    "nic": f["nic"],
                }
                for f in filas
                if f["nic"] not in (None, nic)
            ]

            fugas += len(filtraciones)

            escenarios.append(
                {
                    "nic_consultado": nic,
                    "pregunta": pregunta,
                    "filtraciones": filtraciones,
                    "ok": len(filtraciones) == 0,
                }
            )

    return {
        "escenarios": len(escenarios),
        "fugas": fugas,
        "porcentaje_fugas": round(
            fugas / max(len(escenarios), 1) * 100,
            2,
        ),
        "detalle": escenarios,
    }


# ---------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--repeticiones",
        type=int,
        default=5,
    )

    parser.add_argument(
        "--top",
        type=int,
        default=3,
    )

    args = parser.parse_args()

    salida_json = (
        RAIZ
        / "docs"
        / "evidencias"
        / "experimento-e5-resultados.json"
    )

    salida_csv = (
        RAIZ
        / "docs"
        / "evidencias"
        / "experimento-e5-detalle.csv"
    )

    salida_json.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    print("Calentando modelo de embeddings...")

    rag.embeber(
        ["prueba de calentamiento del modelo"]
    )

    print("Conectando a copiloto-db...")

    with psycopg.connect(
        rag.DB_RO,
        row_factory=dict_row,
    ) as con:

        fragmentos = con.execute(
            "SELECT count(*) AS n FROM fragmentos"
        ).fetchone()["n"]

        if fragmentos == 0:
            raise RuntimeError(
                "La tabla fragmentos está vacía. "
                "Ejecute primero python -m copiloto.ingesta"
            )

        resultados = {}
        detalle_completo = []

        for nombre, funcion in ESTRATEGIAS.items():

            print(
                f"Evaluando estrategia: {nombre}"
            )

            resultado, detalle = evaluar_estrategia(
                nombre,
                funcion,
                con,
                args.repeticiones,
                args.top,
            )

            resultados[nombre] = resultado

            detalle_completo.extend(detalle)

        print("Evaluando aislamiento multi-tenant...")

        aislamiento = evaluar_aislamiento(con)

    vectorial = resultados["vectorial"]
    lexico = resultados["lexico"]
    hibrido = resultados["hibrido_rrf"]

    mejor_hit3_individual = max(
        vectorial["hit_at_3"]["porcentaje"],
        lexico["hit_at_3"]["porcentaje"],
    )

    mejor_mrr_individual = max(
        vectorial["mrr_at_3"],
        lexico["mrr_at_3"],
    )

    criterios = {
        "h1_hit3": (
            hibrido["hit_at_3"]["porcentaje"]
            >= mejor_hit3_individual
        ),

        "h1_mrr": (
            hibrido["mrr_at_3"]
            >= mejor_mrr_individual
        ),

        "h1_latencia_p95_menor_200ms": (
            hibrido["latencia_total_ms"]["p95"]
            < 200
        ),

        "h2_cero_fugas_cross_tenant": (
            aislamiento["fugas"] == 0
        ),
    }

    h1_validada = all(
        [
            criterios["h1_hit3"],
            criterios["h1_mrr"],
            criterios[
                "h1_latencia_p95_menor_200ms"
            ],
        ]
    )

    h2_validada = criterios[
        "h2_cero_fugas_cross_tenant"
    ]

    documento = {
        "experimento": "IA-E1 Benchmark RAG Voltera",

        "fecha_utc": datetime.now(
            timezone.utc
        ).isoformat(),

        "git_commit": commit_actual(),

        "configuracion": {
            "embedding_model": rag.MODELO,
            "rrf_k": rag.K_RRF,
            "preguntas_doradas": len(DORADO),
            "repeticiones_por_pregunta": args.repeticiones,
            "top_k_evaluado": args.top,
            "fragmentos_indexados": fragmentos,
            "nota_latencia": (
                "Modelo calentado previamente. "
                "La latencia total incluye embedding "
                "y SQL, pero excluye cold start."
            ),
        },

        "hipotesis": {
            "H1": (
                "La recuperación híbrida vectorial + "
                "léxica mediante RRF obtiene Hit@3 y "
                "MRR iguales o superiores a las "
                "estrategias individuales, con p95 "
                "total inferior a 200 ms."
            ),

            "H2": (
                "El prefiltrado SQL por NIC evita "
                "recuperar documentos contractuales "
                "de otros clientes."
            ),
        },

        "criterios_exito": {
            "H1": {
                "hit_at_3_hibrido": (
                    ">= mejor estrategia individual"
                ),
                "mrr_hibrido": (
                    ">= mejor estrategia individual"
                ),
                "p95_total_ms": "< 200",
            },

            "H2": {
                "fugas_cross_tenant": 0,
            },
        },

        "resultados": resultados,

        "aislamiento_multi_tenant": aislamiento,

        "veredicto": {
            "H1": (
                "VALIDADA"
                if h1_validada
                else "NO_VALIDADA"
            ),

            "H2": (
                "VALIDADA"
                if h2_validada
                else "NO_VALIDADA"
            ),

            "criterios": criterios,
        },
    }

    with salida_json.open(
        "w",
        encoding="utf-8",
    ) as f:

        json.dump(
            documento,
            f,
            ensure_ascii=False,
            indent=2,
        )

    if detalle_completo:

        columnas = list(
            detalle_completo[0].keys()
        )

        with salida_csv.open(
            "w",
            newline="",
            encoding="utf-8-sig",
        ) as f:

            writer = csv.DictWriter(
                f,
                fieldnames=columnas,
            )

            writer.writeheader()

            for fila in detalle_completo:

                fila = fila.copy()

                fila["fuentes_recuperadas"] = (
                    " | ".join(
                        fila["fuentes_recuperadas"]
                    )
                )

                writer.writerow(fila)

    print()
    print("======================================")
    print("RESULTADOS")
    print("======================================")

    for nombre, r in resultados.items():

        print()
        print(nombre)

        print(
            " Hit@1:",
            f'{r["hit_at_1"]["porcentaje"]}%'
        )

        print(
            " Hit@3:",
            f'{r["hit_at_3"]["porcentaje"]}%'
        )

        print(
            " MRR@3:",
            r["mrr_at_3"],
        )

        print(
            " p95 total:",
            f'{r["latencia_total_ms"]["p95"]} ms'
        )

    print()
    print(
        "Fugas cross-tenant:",
        aislamiento["fugas"],
    )

    print(
        "H1:",
        documento["veredicto"]["H1"],
    )

    print(
        "H2:",
        documento["veredicto"]["H2"],
    )

    print()
    print(
        "JSON:",
        salida_json,
    )

    print(
        "CSV:",
        salida_csv,
    )


if __name__ == "__main__":
    main()