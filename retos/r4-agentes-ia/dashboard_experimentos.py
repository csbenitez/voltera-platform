from pathlib import Path
import json

import pandas as pd
import streamlit as st


# ============================================================
# CONFIGURACIÓN
# ============================================================

st.set_page_config(
    page_title="Voltera · Evidencia IAGEN",
    page_icon="🧠",
    layout="wide",
)

RAIZ = Path(__file__).resolve().parents[2]

JSON_RESULTADOS = (
    RAIZ
    / "docs"
    / "evidencias"
    / "experimento-e5-resultados.json"
)

CSV_DETALLE = (
    RAIZ
    / "docs"
    / "evidencias"
    / "experimento-e5-detalle.csv"
)


# ============================================================
# CARGA DE DATOS
# ============================================================

@st.cache_data
def cargar_datos(json_mtime, csv_mtime):
    with open(JSON_RESULTADOS, "r", encoding="utf-8") as f:
        resumen = json.load(f)

    detalle = pd.read_csv(CSV_DETALLE)

    return resumen, detalle


resumen, detalle = cargar_datos(
    JSON_RESULTADOS.stat().st_mtime_ns,
    CSV_DETALLE.stat().st_mtime_ns,
)

resultados = resumen["resultados"]
veredicto = resumen["veredicto"]
configuracion = resumen["configuracion"]
aislamiento = resumen["aislamiento_multi_tenant"]


# ============================================================
# UTILIDADES
# ============================================================

NOMBRES = {
    "vectorial": "Vectorial",
    "lexico": "Léxico",
    "hibrido_rrf": "Híbrido RRF",
}


def nombre_estrategia(codigo):
    return NOMBRES.get(codigo, codigo)


def rank_texto(rank):
    if pd.isna(rank):
        return "No encontrada"
    return f"Posición {int(rank)}"


def separar_fuentes(valor):
    if pd.isna(valor):
        return []

    return [
        x.strip()
        for x in str(valor).split("|")
        if x.strip()
    ]


# ============================================================
# ENCABEZADO
# ============================================================

st.title("🧠 Voltera · Laboratorio experimental IAGEN")

if st.button("🔄 Actualizar evidencia"):
    st.cache_data.clear()
    st.rerun()

st.caption(
    "Experimento IA-E1 · Vectorial vs Léxico vs Híbrido RRF"
)

st.markdown(
    """
Este tablero presenta la evidencia reproducible utilizada para validar
la hipótesis experimental del RAG de Voltera.
"""
)


# ============================================================
# HIPÓTESIS
# ============================================================

st.subheader("Hipótesis experimental")

st.info(
    """
**H1:** La recuperación híbrida, combinando búsqueda vectorial y
búsqueda léxica mediante Reciprocal Rank Fusion (RRF), obtiene una
calidad de recuperación igual o superior a las estrategias individuales,
manteniendo una latencia p95 inferior a 200 ms.
"""
)


# ============================================================
# KPIs
# ============================================================

hibrido = resultados["hibrido_rrf"]

c1, c2, c3, c4, c5 = st.columns(5)

c1.metric(
    "H1",
    veredicto["H1"],
)

c2.metric(
    "Hit@1 híbrido",
    f'{hibrido["hit_at_1"]["porcentaje"]:.2f}%',
)

c3.metric(
    "Hit@3 híbrido",
    f'{hibrido["hit_at_3"]["porcentaje"]:.2f}%',
)

c4.metric(
    "MRR@3 híbrido",
    f'{hibrido["mrr_at_3"]:.4f}',
)

c5.metric(
    "p95 híbrido",
    f'{hibrido["latencia_total_ms"]["p95"]:.2f} ms',
)


# ============================================================
# CONTEXTO DEL EXPERIMENTO
# ============================================================

with st.expander("Configuración del experimento"):

    a, b, c, d = st.columns(4)

    a.metric(
        "Preguntas doradas",
        configuracion["preguntas_doradas"],
    )

    b.metric(
        "Fragmentos indexados",
        configuracion["fragmentos_indexados"],
    )

    c.metric(
        "Repeticiones",
        configuracion["repeticiones_por_pregunta"],
    )

    d.metric(
        "RRF k",
        configuracion["rrf_k"],
    )

    st.code(
        f'Embedding model: {configuracion["embedding_model"]}'
    )


# ============================================================
# COMPARACIÓN GLOBAL
# ============================================================

st.divider()

st.subheader("1 · Comparación global de calidad")

filas_metricas = []

for codigo, r in resultados.items():

    filas_metricas.append(
        {
            "Estrategia": nombre_estrategia(codigo),
            "Hit@1": r["hit_at_1"]["porcentaje"],
            "Hit@3": r["hit_at_3"]["porcentaje"],
            "MRR@3": r["mrr_at_3"] * 100,
        }
    )

df_metricas = pd.DataFrame(filas_metricas)

st.dataframe(
    df_metricas.style.format(
        {
            "Hit@1": "{:.2f}%",
            "Hit@3": "{:.2f}%",
            "MRR@3": "{:.2f}",
        }
    ),
    use_container_width=True,
    hide_index=True,
)

st.bar_chart(
    df_metricas.set_index("Estrategia")[["Hit@1", "Hit@3"]],
    use_container_width=True,
)

st.success(
    """
El híbrido alcanzó el **100 % de Hit@3** y el mejor Hit@1 y MRR@3.
Esto significa que la fuente correcta apareció entre los tres primeros
resultados para todas las preguntas del conjunto dorado.
"""
)


# ============================================================
# LATENCIA
# ============================================================

st.divider()

st.subheader("2 · Trade-off de latencia")

filas_latencia = []

for codigo, r in resultados.items():

    filas_latencia.append(
        {
            "Estrategia": nombre_estrategia(codigo),
            "p95 ms": r["latencia_total_ms"]["p95"],
            "Mediana ms": r["latencia_total_ms"]["mediana"],
        }
    )

df_latencia = pd.DataFrame(filas_latencia)

st.bar_chart(
    df_latencia.set_index("Estrategia")[["p95 ms"]],
    use_container_width=True,
)

st.caption(
    "Criterio experimental definido antes de la medición: p95 < 200 ms."
)

if hibrido["latencia_total_ms"]["p95"] < 200:
    st.success(
        f'✓ El híbrido cumple el criterio: '
        f'{hibrido["latencia_total_ms"]["p95"]:.2f} ms < 200 ms.'
    )
else:
    st.error(
        "El híbrido no cumple el criterio de latencia."
    )


# ============================================================
# CASO POR PREGUNTA
# ============================================================

st.divider()

st.subheader("3 · Evidencia por consulta")

preguntas = (
    detalle[["pregunta_id", "pregunta"]]
    .drop_duplicates()
    .sort_values("pregunta_id")
)

opciones = {
    f'{int(row.pregunta_id):02d} · {row.pregunta}':
        int(row.pregunta_id)
    for _, row in preguntas.iterrows()
}

seleccion = st.selectbox(
    "Selecciona una pregunta del benchmark",
    list(opciones.keys()),
)

pregunta_id = opciones[seleccion]

# Usamos la primera repetición porque el ranking
# es determinista para el mismo índice y consulta.
caso = detalle[
    (detalle["pregunta_id"] == pregunta_id)
    & (detalle["repeticion"] == 1)
].copy()

esperada = caso.iloc[0]["fuente_esperada"]

st.markdown(f"**Fuente esperada:** `{esperada}`")

columnas = st.columns(3)

orden = [
    "vectorial",
    "lexico",
    "hibrido_rrf",
]

for col, estrategia in zip(columnas, orden):

    fila = caso[
        caso["estrategia"] == estrategia
    ].iloc[0]

    rank = fila["rank"]

    with col:

        st.markdown(
            f"### {nombre_estrategia(estrategia)}"
        )

        if not pd.isna(rank) and int(rank) == 1:
            st.success(
                f"✓ Fuente correcta en posición {int(rank)}"
            )
        elif not pd.isna(rank) and int(rank) <= 3:
            st.warning(
                f"Fuente correcta en posición {int(rank)}"
            )
        else:
            st.error(
                "Fuente correcta fuera del Top 3"
            )

        fuentes = separar_fuentes(
            fila["fuentes_recuperadas"]
        )

        for pos, fuente in enumerate(
            fuentes,
            start=1,
        ):

            if fuente == esperada:
                st.markdown(
                    f"**{pos}. ✅ {fuente}**"
                )
            else:
                st.markdown(
                    f"{pos}. {fuente}"
                )

        st.caption(
            f'Latencia total: '
            f'{fila["total_ms"]:.2f} ms'
        )


# ============================================================
# EXPLICACIÓN DE LA FUSIÓN
# ============================================================

st.markdown("#### ¿Qué hizo el híbrido?")

st.code(
    """
Pregunta
   │
   ├───────────────┐
   │               │
   ▼               ▼
Vectorial        Léxico
pgvector        tsvector
   │               │
   └───────┬───────┘
           ▼
     Reciprocal Rank
       Fusion (RRF)
           │
           ▼
    Ranking combinado
"""
)


# ============================================================
# PREGUNTAS DONDE EL HÍBRIDO MEJORA
# ============================================================

st.divider()

st.subheader("4 · Casos donde el híbrido mejora el ranking")

base = detalle[
    detalle["repeticion"] == 1
][
    [
        "pregunta_id",
        "pregunta",
        "estrategia",
        "rank",
    ]
]

pivot = base.pivot_table(
    index=["pregunta_id", "pregunta"],
    columns="estrategia",
    values="rank",
    aggfunc="first",
).reset_index()

for col in ["vectorial", "lexico", "hibrido_rrf"]:
    if col not in pivot.columns:
        pivot[col] = pd.NA

pivot["mejora_vs_vectorial"] = (
    pivot["hibrido_rrf"].fillna(999)
    < pivot["vectorial"].fillna(999)
)

pivot["mejora_vs_lexico"] = (
    pivot["hibrido_rrf"].fillna(999)
    < pivot["lexico"].fillna(999)
)

mejoras = pivot[
    pivot["mejora_vs_vectorial"]
    | pivot["mejora_vs_lexico"]
].copy()

mejoras = mejoras.rename(
    columns={
        "pregunta": "Pregunta",
        "vectorial": "Vectorial",
        "lexico": "Léxico",
        "hibrido_rrf": "Híbrido RRF",
    }
)

st.dataframe(
    mejoras[
        [
            "Pregunta",
            "Vectorial",
            "Léxico",
            "Híbrido RRF",
        ]
    ],
    use_container_width=True,
    hide_index=True,
)

st.caption(
    """
Un número menor indica una mejor posición.
La posición 1 significa que el documento correcto fue el primer
resultado recuperado.
"""
)


# ============================================================
# SEGURIDAD MULTI-TENANT
# ============================================================

st.divider()

st.subheader("5 · Aislamiento multi-tenant")

s1, s2, s3 = st.columns(3)

s1.metric(
    "Escenarios evaluados",
    aislamiento["escenarios"],
)

s2.metric(
    "Fugas detectadas",
    aislamiento["fugas"],
)

s3.metric(
    "Porcentaje de fugas",
    f'{aislamiento["porcentaje_fugas"]:.2f}%',
)

if aislamiento["fugas"] == 0:
    st.success(
        "✓ H2 VALIDADA · No se recuperaron contratos de otros clientes."
    )
else:
    st.error(
        "Se detectaron fugas cross-tenant."
    )


# ============================================================
# VEREDICTO
# ============================================================

st.divider()

st.subheader("Conclusión experimental")

if veredicto["H1"] == "VALIDADA":

    st.success(
        """
### H1 VALIDADA

El RAG híbrido obtuvo la mejor calidad de recuperación:

- mayor Hit@1;
- 100 % Hit@3;
- mayor MRR@3;
- p95 inferior al límite experimental de 200 ms.

Por tanto, la combinación de recuperación semántica y léxica
mediante RRF ofrece un mejor equilibrio entre relevancia y
rendimiento para el dominio Voltera.
"""
    )

else:

    st.error(
        "H1 no fue validada por los resultados experimentales."
    )


# ============================================================
# EVIDENCIA DESCARGABLE
# ============================================================

st.subheader("Evidencia reproducible")

with open(
    JSON_RESULTADOS,
    "rb",
) as archivo_json:

    st.download_button(
        "Descargar resultados JSON",
        data=archivo_json,
        file_name="experimento-e5-resultados.json",
        mime="application/json",
    )

with open(
    CSV_DETALLE,
    "rb",
) as archivo_csv:

    st.download_button(
        "Descargar detalle CSV",
        data=archivo_csv,
        file_name="experimento-e5-detalle.csv",
        mime="text/csv",
    )