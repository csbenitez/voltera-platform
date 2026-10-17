# Knative en el lab: ruteo de declaraciones y cómo verlo en Application Insights

Estado: **implementado, sin desplegar ni probar en cluster**. Todo lo que se dice abajo sobre qué aparece en
Application Insights es lo que el diseño debería producir; se confirma al correr `knative-test` (ver §6).

Diseño y justificación: [`PLAN_SIMPLIFICACION.md`](PLAN_SIMPLIFICACION.md). Este documento explica cómo
operarlo y cómo leer la interoperabilidad en App Insights.

## 1. Qué hace

Un medidor `AMI-cloud-0007` que se declara en el head-end on-prem no se procesa allí. El
`declaration-gateway` on-prem lo reenvía a la nube **por Knative**; el Event Mesh (`mesh-bridge` →
`mesh-router`) sigue transportando los `EnergyMatched`, sin cambios.

```
gateway onprem ──outbox──► topic forward ──KafkaSource──HTTPS──► Broker ──Trigger──► KafkaSink ──► topic ingress ──► gateway nube
 (span "knative forward cloud")            (Knative)             (Knative, nube)                                      (span "process ... voltera.forwarded_from=onprem")
```

## 2. Qué verás en Application Insights

Hay **dos tipos de spans** en este camino:

| Origen | Spans | Garantía |
|---|---|---|
| Código del lab | `inject declaration`, `publish voltera.declaration.forward.v1`, **`knative forward cloud`**, `process voltera.ingress.declarations` (con `voltera.forwarded_from=onprem`) | Siempre aparecen |
| Componentes de Knative (KafkaSource, Broker, Trigger, KafkaSink) | Roles que empiezan por `knative`/`kafka-` | **Best-effort**: dependen de que `config-observability` apunte al collector por OTLP (lo hace `addons -Knative`) y de la versión del dataplane |

Por eso el salto es visible aunque Knative no exporte nada: el span `knative forward <región>` marca la
salida, y el `process` de la nube lleva `voltera.forwarded_from` y `voltera.channel=knative`.

### Atributos que distinguen el canal

| Atributo (`customDimensions`) | Valor | Qué te dice |
|---|---|---|
| `voltera.channel` | `knative` | El evento viajó por Knative (y no por el mesh propio) |
| `voltera.forwarded_from` | `onprem` | Región donde se declaró |
| `voltera.home_region` | `cloud` | Región dueña del medidor (destino) |
| `messaging.message.id` | el `ce-id` | **Une las dos trazas** aunque se corte el `traceparent` |
| `cloud.region` / `voltera.origin_region` | `onprem` / `cloud` | Región del servicio / origen del hecho |

### Cómo leer la traza (esperada)

```
inject declaration                    injector            onprem
 └ publish voltera.ingress...         injector            onprem
    └ process voltera.ingress...      declaration-gateway onprem
       └ knative forward cloud        declaration-gateway onprem   ← salida del canal Knative
          └ publish ...forward.v1     declaration-gateway onprem
             ·· (KafkaSource → Broker → Trigger → KafkaSink: spans knative-*, si se exportan)
             └ process voltera.ingress...   declaration-gateway cloud   forwarded_from=onprem
                └ publish position → process position → match FCFS → ...
```

* El **hueco de tiempo** entre `publish ...forward.v1` (onprem) y el `process` de la nube es la latencia
  del canal Knative completo: KafkaSource + HTTPS + Broker + Trigger + KafkaSink.
* Si el `process` de la nube aparece **en otra traza** (otro `operation_Id`), Knative cortó el
  `traceparent`. No se pierde la correlación: usa la consulta 2 de abajo con el `ce-id`.
* Un evento filtrado por el Trigger (`homeregion` distinto de `cloud`) **no genera** `process` en la nube.
  Los fallidos terminan en `voltera.declaration.dlq.v1` y no tienen `process`.

## 3. Consultas

`.\scripts\lab.ps1 knative-kql` (o `./scripts/lab.sh knative-kql`) las imprime. En App Insights → **Logs**:

1. **Vista general del canal**: spans con `voltera.channel == "knative"`, el rol y la región.
2. **Una declaración de punta a punta** por `ce-id`: reemplaza `<ce-id>`; funciona aunque haya dos trazas.
3. **¿Se cortó el traceparent?**: cuenta declaraciones con una traza (`unidas`) frente a varias (`partidas`).

Además, **Application Map** debe mostrar `declaration-gateway (onprem)` conectado con
`declaration-gateway (cloud)` cuando el `traceparent` se propaga, y nodos `knative-*` si los exporta.

## 4. Cómo interpretar los números

| Observas | Significa |
|---|---|
| `knative forward` > `process ... forwarded_from=onprem` | Hay eventos en tránsito o filtrados/en DLQ. Mira `status` (lag del KafkaSource, DLQ) |
| `unidas` ≈ total | Knative propaga el `traceparent`: la traza es continua |
| `partidas` ≈ total | El `traceparent` se pierde en Knative: la correlación es solo por `ce-id` |
| Latencia del hueco ≫ latencia del mesh | Es el costo de las capas adicionales de Knative (resultado de K7) |

## 5. Despliegue

```powershell
.\scripts\lab.ps1 addons -Target all -Knative    # Knative Eventing + eventing-kafka-broker
.\scripts\lab.ps1 up -Target all                 # aplica además deploy\knative\<región>
.\scripts\lab.ps1 status                         # Broker/Trigger/KafkaSink (nube) y KafkaSource (onprem)
```

* `addons -Knative` instala las versiones de `infra/addons/versions.env` (`KNATIVE_VERSION`,
  `KNATIVE_KAFKA_VERSION`) y apunta `config-observability` (`tracing-endpoint`) al puerto OTLP/HTTP 4318 del `otel-collector`.
  También baja los `requests` de CPU de los data planes de Kafka (piden 1 CPU y el nodo k3d tiene 2) y apaga
  `job-sink` y `request-reply`, que el lab no usa.
* `cleanup` ya **no** borra Knative Eventing; solo Serving y restos de la arquitectura anterior.
* Costo: Knative Eventing vuelve a ocupar RAM en los nodos (ver `FINOPS_AKS.md`). Medir con
  `kubectl top nodes` antes y después.

## 6. Prueba

```powershell
.\scripts\lab.ps1 knative-test            # inyecta 100 declaraciones con 20 % de medidores ajenos
.\scripts\lab.ps1 inject -Target onprem -N 500 -Foreign 0.3
```

`knative-test` compara las declaraciones reenviadas por el gateway on-prem (topic `forward`) con las que
llegan a la cola de ingesta de la nube y con la DLQ. `PASS` si coinciden y la DLQ no crece. Después imprime
las consultas KQL; espera de 1 a 3 minutos a la ingesta de App Insights antes de ejecutarlas.

> La comparación asume que la nube no recibe inyección propia durante la prueba.

## 7. Limitaciones conocidas

* No verificado en cluster: versiones de Knative, nombres de los objetos del dataplane y si KafkaSource
  copia `traceparent` como extensión. El lab está preparado para ambos resultados (§2).
* El token entre regiones viaja como extensión CE (`Ce-Meshtoken`): compromiso de laboratorio.
* Solo la dirección on-prem → nube (k3d no es alcanzable desde AKS).
