---
titulo: Cómo funciona el intercambio de energía entre vecinos (P2P)
tipo: procedimiento
---

# Cómo funciona el intercambio de energía entre vecinos (P2P)

Documento sintético de demostración.

## Declaración y calce

Cada medidor inteligente (identificador AMI-región-número) declara periódicamente si tiene excedente (SURPLUS) o déficit (DEFICIT) de energía. El motor de calce empareja excedentes con déficits por orden de llegada y al precio por kWh vigente en ese momento. Cada emparejamiento es un calce de energía (EnergyMatched) con vendedor, comprador, kWh, precio y total.

## De los calces a la factura

Los calces de ambas regiones se consolidan sin duplicados y llegan a facturación. En la factura mensual:

- los calces donde el medidor del cliente fue comprador suman a la línea compra_p2p;
- los calces donde fue vendedor suman a la línea venta_p2p.

Por eso, cuando un cliente pregunta por una compra_p2p alta, el asesor revisa los calces recientes de su medidor: cuántos hubo, cuántos kWh compró y a qué precio.

## Precio del calce

El precio por kWh se publica cada pocos segundos y varía. El precio que se cobra es el vigente en el instante del calce, no un promedio del mes.

## Continuidad

Si el motor de calce de una región deja de responder, la otra región procesa sus posiciones. El calce queda marcado con la región de origen y la región que lo procesó; para el cliente no cambia nada en la factura.
