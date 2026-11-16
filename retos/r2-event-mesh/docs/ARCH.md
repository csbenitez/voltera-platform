PROMPT DE ARQUITECTURA: LAB / POC DE EVENT-DRIVEN ARCHITECTURE (EDA) CON MOCKS

Actúa como Arquitecto de Software y Desarrollador Lead para definir e implementar un laboratorio práctico (Lab/PoC) que valide la Arquitectura Dirigida por Eventos (EDA) y la capa de Event Mesh de un sistema P2P de energía. Cada servicio del sistema se implementará como un MOCK o simulador liviano (ej. en Docker Compose / Local K3d).

### 1. OBJETIVO DEL LAB
- Demostrar el flujo asíncrono e inmutable de eventos de punta a punta entre componentes mockeados.
- Validar el desacoplamiento Pub/Sub mediante colas/brokers locales (Redpanda).
- Simular la gobernanza y enrutamiento multirregión a través de una instancia ligera de Event Mesh. El local simula on-premise y AKS nube (finops para experimientos).

### 2. COMPONENTES Y SUS MOCKS / CONECTORES EDA

1. declaration-gateway (Mock Microservicio)
   - Función Mock: Emula la ingesta de telemetría procesando o generando posiciones sintéticas de prueba (SURPLUS / DEFICIT).
   - Conector de Entrada (Ingress Queue): Suscrito a la "Cola de Declaración de Posición".
   - Conector de Salida (Outbox Broker): Publica el evento mock 'PositionDeclared' hacia el broker local.
   - Simulación KEDA: Script/métrica sintética para emular la prueba de autoescalado (0 a N réplicas) según la acumulación de mensajes en la cola.

2. price-feed-simulator (Mock Simulador de Precios)
   - Función Mock: Script liviano que genera tarifas \$/kWh aleatorias/programadas en intervalos configurables.
   - Conector de Salida (Event Publisher): Emite periódicamente el evento 'Precio kWh' hacia la cola/broker local.

3. market-projector (Mock Proyector CQRS)
   - Función Mock: Procesa los eventos entrantes y actualiza una base de datos en memoria (ej. Redis/SQLite) que actúa como vista de lectura con TTL.
   - Conectores de Entrada (Event Consumers): Suscrito a las colas 'PositionDeclared' y 'Precio kWh'.

4. matching-engine (Mock Motor de Matching)
   - Función Mock: Ejecuta un algoritmo de calce simplificado (ej. First-Come-First-Served o reglas estáticas en JSON) sobre el almacén en memoria.
   - Conector de Salida (Outbox Broker): Publica el evento 'EnergyMatched' hacia la cola local tras registrar el calce mock.
   - Para simplificar puedes unificar market-projector y matching-engine en uno solo. Por lo que ya tendrias el conector de entrada.

5. energy-transfer (Mock Operativo)
   - Función Mock: Simula la recepción de orden de calce y escribe en logs o consola emulando la emisión de comandos hacia los switches físicos (sin hardware real).
   - Conector de Entrada (Event Consumer): Suscrito a la cola 'EnergyMatched'.

6. Event Mesh (Mock Bus Global & Router)
   - Función Mock: Instancia centralizada de broker/router encargado de la autenticación, observabilidad y reenvío del evento 'EnergyMatched'.
   - Conector de Ingesta: Suscrito a los eventos 'EnergyMatched' provenientes de la Región 1 y Región 2 (simuladas en contenedores separados).
   - Conector de Salida: Enruta los eventos consolidados hacia el servicio mock de Facturación.

7. Facturación (Mock Sistema Externo)
   - Función Mock: Consumer dummy/endpoint de log que valida y registra los cierres de calce entregados por el Event Mesh.

### 3. FLUJO DE EJECUCIÓN DEL LAB

1. Inyección Sintética: Un script de prueba inyecta N mensajes a la Cola de Declaración simulando medidores AMI.
2. Ingesta y Outbox: `declaration-gateway` (mock) consume la cola, persiste en DB local y emite `PositionDeclared`.
3. Proyección de Mercado: `price-feed-simulator` emite `Precio kWh` -> `market-projector` (mock) actualiza la vista de lectura en memoria.
4. Emparejamiento: `matching-engine` (mock) ejecuta el calce -> emite `EnergyMatched` -> `energy-transfer` (mock) registra el comando de conmutación en logs.
5. Consolidación Global: `Event Mesh` (mock) intercepta `EnergyMatched` y lo entrega a `Facturación` (mock) para cerrar la prueba de concepto.

 ## 4. Medicion
1. Medir traza para validar interoperabilidad (puede ser desde app insight o grafana).  
2. Prueba constante donde si apago el matching-engine local el event mesh reenvia los mensajes al matching-engine en nube.


