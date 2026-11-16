---
titulo: Preguntas frecuentes de pagos
tipo: faq
---

# Preguntas frecuentes de pagos

Documento sintético de demostración.

## Medios de pago

Cada cliente tiene registrado un instrumento de pago: tarjeta (card), transferencia bancaria (bank_transfer) o billetera (wallet). El instrumento determina la pasarela: las tarjetas y billeteras van por gateway-card y las transferencias por gateway-bank. El cliente no elige la pasarela.

## Estados de un pago

- **SUCCEEDED**: la pasarela aprobó el pago.
- **PENDING**: el pago está en curso; puede tardar unos minutos.
- **FAILED**: la pasarela lo rechazó. No se descontó dinero.

## Ya pagué y la factura sigue apareciendo pendiente

Es el caso más común. El asesor consulta los pagos asociados a la factura (la referencia del pago es el identificador de la factura):

- Si hay un pago aprobado y la factura sigue pendiente, es un desfase de conciliación. El asesor confirma al cliente que el pago sí se recibió, le da la referencia del proveedor y abre un reclamo de prioridad alta para que se actualice el estado. El cliente no debe pagar de nuevo.
- Si no hay ningún pago con esa referencia, el pago no llegó a Voltera. El asesor pide el comprobante y verifica que la referencia usada sea el identificador correcto de la factura.
- Si el pago fue rechazado, el asesor informa el motivo y ofrece registrar un nuevo pago.

## Pagos duplicados

Cada pago lleva una clave de idempotencia. Si el cliente o el sistema reintenta con la misma clave, no se cobra dos veces: la pasarela devuelve el resultado del primer intento. Dos pagos con claves distintas para la misma factura sí son dos cobros y dan lugar a devolución.

## Registrar un pago desde el call center

El asesor puede registrar un pago por el valor total de la factura con el instrumento que el cliente ya tiene registrado. Requiere confirmación explícita del asesor antes de ejecutarse. No se aceptan pagos parciales por este canal.

## Fecha límite de pago

La factura vence el día 20 del mes siguiente al periodo facturado. Después de esa fecha se cobra interés de mora, salvo que exista un reclamo abierto sobre la factura.
