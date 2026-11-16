---
titulo: Procedimiento de verificación de integridad de una factura
tipo: procedimiento
---

# Procedimiento de verificación de integridad de una factura

Documento sintético de demostración, basado en la especificación de huella de Voltera.

## Qué se verifica

Al emitir una factura, Voltera calcula su huella (SHA-256 del JSON canónico) y la ancla en un libro compartido con el operador de red, el gestor de comunidades y el ente de control. Verificar es recalcular la huella del documento guardado y compararla con la anclada. La consulta se hace contra el nodo de otra organización, no contra el de Voltera.

## Veredictos

| Veredicto | Significado | Qué hace el asesor |
|---|---|---|
| ÍNTEGRA | El documento coincide con lo anclado | Puede confirmar los valores al cliente y citar la transacción como evidencia |
| ALTERADA | El documento guardado no coincide con la huella anclada, falta o no se puede leer | No confirma valores; abre reclamo de prioridad alta |
| NO ANCLADA | No hay registro de la factura en la cadena | Revisa la fecha de emisión; ver política de reclamos |
| SIN VEREDICTO | La cadena no respondió | No concluye nada; reintenta más tarde |

## Motivos de ALTERADA

- huella_distinta: cambió algún dato (un peso, un Wh, el periodo, una línea).
- pdf_distinto: el PDF no corresponde al JSON anclado.
- documento_ausente o pdf_documento_ausente: el documento fue eliminado.
- cifrado_invalido o pdf_cifrado_invalido: el archivo cifrado fue manipulado.
- json_no_canonicalizable: el documento tiene claves duplicadas o números no enteros.

## Evidencia que se entrega al cliente

El identificador de la factura, la huella calculada, la huella en la cadena y el hash de la transacción de anclaje. Con esos datos cualquier participante del libro puede repetir la verificación.

## Qué no cubre la verificación

La huella prueba que el documento no cambió después de emitido. No prueba que la medición del medidor fuera correcta: un error anterior al cálculo de la huella queda sellado tal cual. Para dudas sobre el consumo se revisan los calces de energía del medidor.
