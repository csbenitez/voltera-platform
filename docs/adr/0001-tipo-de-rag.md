# ADR 0001 · Tipo de RAG del copiloto de facturación

Estado: aceptado · 2026-11-16

## Contexto

El asesor del call center necesita respuestas sobre tarifas, política de reclamos, procedimientos y el contrato del cliente que tiene en línea. Las preguntas mezclan lenguaje natural ("¿por qué subió la factura?") con códigos exactos (`compra_p2p`, `huella_distinta`, `NO ANCLADA`, un NIC). El corpus es pequeño (12 documentos, 84 fragmentos) y parte de él es privado por cliente.

Los datos transaccionales (facturas, pagos, calces, veredictos de la cadena) no son conocimiento documental: cambian a cada minuto y tienen una fuente de verdad. No van al RAG; se consultan por MCP.

## Opciones evaluadas

| Tipo | A favor | En contra | Veredicto |
|---|---|---|---|
| RAG ingenuo (solo vectores) | Simple | Falla con códigos exactos e identificadores; no distingue `ALTERADA` de `NO ANCLADA` por semántica | Descartado |
| Solo léxico (BM25 / texto completo) | Exacto con códigos | No entiende paráfrasis ("ya pagué y me siguen cobrando") | Descartado |
| GraphRAG | Bueno para preguntas globales sobre corpus grandes y relacionales | Indexación costosa en llamadas al LLM; sin beneficio con 12 documentos | Descartado |
| Contexto largo / CAG (todo el corpus en el prompt) | Sin infraestructura | No filtra por cliente, gasta la cuota gratuita en cada turno y no escala | Descartado |
| **RAG agéntico con recuperación híbrida** | Cubre códigos y paráfrasis; filtra por cliente; se corrige si recupera mal | Dos llamadas más al LLM por consulta | **Elegido** |

## Decisión

1. **Recuperación híbrida** en un solo Postgres (`pgvector`): vectores de 384 dimensiones con un modelo multilingüe local (`paraphrase-multilingual-MiniLM-L12-v2` vía `fastembed`) y texto completo en español (`tsvector`). Los dos rankings se fusionan con Reciprocal Rank Fusion (k = 60).
2. **Filtro por metadatos antes de rankear**: un fragmento con `nic` solo es visible cuando la consulta es de ese cliente. El filtro está en el SQL, no en el prompt.
3. **Ciclo correctivo corto**: el agente de conocimiento califica si los fragmentos bastan; si no, reformula la consulta una vez y vuelve a buscar. Responde citando `[n]` por afirmación.
4. **Embeddings locales**: no dependen del proveedor del LLM ni consumen cuota.

Implementación: `retos/r4-agentes-ia/copiloto/rag.py` (recuperación), `ingesta.py` (índice) y el nodo `conocimiento` de `grafo.py`.

## Consecuencias

- Un solo motor (Postgres) para CRM, vectores y texto completo: menos piezas que operar.
- El conjunto dorado (`tests/test_rag_dorado.py`, 15 preguntas) exige la fuente esperada en el top 3 y verifica que nunca se devuelva el contrato de otro cliente.
- La consulta léxica une los términos con OR; con un corpus mucho mayor habría que añadir un re-ranker.
- Si el corpus crece a miles de documentos con relaciones entre ellos, se reevalúa GraphRAG.
