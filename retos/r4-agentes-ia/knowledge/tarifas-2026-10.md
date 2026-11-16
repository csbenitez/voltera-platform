---
titulo: Tarifas vigentes octubre 2026 (versión 2026-10-v1)
tipo: tarifa
---

# Tarifas vigentes octubre 2026 (versión 2026-10-v1)

Documento sintético de demostración. Los valores no corresponden a tarifas reales.

## Conceptos que aparecen en la factura

Cada factura de Voltera tiene una o varias líneas. Cada línea indica el concepto, la energía en Wh y el valor en pesos colombianos (COP). Un valor negativo es un abono a favor del cliente.

- **red**: energía tomada de la red del operador. Es el cargo principal.
- **excedente**: energía que el cliente entregó a la red y no se vendió a un vecino. Es un abono.
- **compra_p2p**: energía comprada a otro miembro de la comunidad energética.
- **venta_p2p**: energía vendida a otro miembro de la comunidad. Es un abono.

## Precios por concepto

| Concepto | Precio | Signo en la factura |
|---|---|---|
| red | entre 700 y 950 COP por kWh, según la franja del mes | cargo |
| excedente | 300 COP por kWh | abono |
| compra_p2p | 500 COP por kWh | cargo |
| venta_p2p | 400 COP por kWh | abono |

El precio de red de cada factura se fija una vez al emitirla y queda dentro del rango publicado. La compra P2P siempre es más barata que la red: si la línea compra_p2p crece, el cliente está pagando menos por kWh, aunque el total suba porque consumió más energía.

## Por qué puede subir una factura

1. Mayor consumo de red (línea red con más Wh que el mes anterior).
2. Mayor compra a vecinos (línea compra_p2p alta): suele ocurrir cuando los paneles del cliente generaron menos.
3. Menos abonos: no hubo excedente ni venta_p2p en el periodo.

El asesor debe comparar las líneas de la factura con los calces de energía del medidor antes de dar una explicación.

## Cómo se calcula el total

Total en COP = suma de las líneas. Cada línea se calcula como Wh × precio ÷ 1000, redondeado al peso. Los totales se expresan en enteros: no hay decimales en COP ni en Wh.
