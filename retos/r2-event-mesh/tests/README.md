# Pruebas

La validación funcional del lab EDA (inyección, KEDA, traza y failover) ya no vive en k6. Se hace con
`scripts/lab.ps1` (`inject`, `status`, `failover-test`, `trace`), descrito en
[`docs/LAB_RUNBOOK.md`](../docs/LAB_RUNBOOK.md). La inyección sintética la hace el binario
`services/cmd/injector`, que corre como Job en el cluster: produce directo en Redpanda y crea una traza
raíz por mensaje.

Esta carpeta conserva el test k6 del **experimento de idempotencia** (match-processor). Es trabajo
independiente y no forma parte del despliegue EDA.

### Paso 04: Test de Idempotencia (`04_idempotency_test.js`) 🎯

**Objetivo**: Validar que el sistema procesa eventos únicos una sola vez, rechazando duplicados automáticamente.

Este test es parte del **Experimento de Idempotencia** y simula:
- 90% de eventos únicos (900 eventos)
- 10% de eventos duplicados (100 eventos con `match_id` repetidos)
- Total: 1000 eventos enviados

#### 4.1 Ejecución con script automatizado (Recomendado):
```bash
# El script maneja todo: limpieza + test + validación
./scripts/idempotency/demo-idempotency.sh
```

#### 4.2 Ejecución manual del test:
```bash
# Prerequisito: match-processor debe estar corriendo
kubectl port-forward -n voltera svc/match-processor 8090:8080

# Ejecutar test en otra terminal
k6 run tests/04_idempotency_test.js \
  -e TARGET_URL="http://localhost:8090/events" \
  -e TOTAL=1000
```

#### 4.3 Validación de resultados:
```bash
# Verificar que NO hay duplicados en DB (debe retornar 0 rows)
POSTGRES_POD=$(kubectl get pod -n voltera -l app=postgres-matches -o jsonpath='{.items[0].metadata.name}')

kubectl exec -n voltera $POSTGRES_POD -- psql -U voltera -d voltera_matches -c "
SELECT match_id, COUNT(*) as count 
FROM matches 
GROUP BY match_id 
HAVING COUNT(*) > 1;
"

# Esperado: (0 rows) ✅
```

#### 4.4 Variables configurables:
| Variable | Descripción | Valor por Defecto |
| :--- | :--- | :--- |
| `TARGET_URL` | URL del servicio match-processor | `http://localhost:8090/events` |
| `TOTAL` | Total de eventos a enviar | `1000` |
| `DUPLICATE_RATE` | Porcentaje de duplicados (0.0-1.0) | `0.10` (10%) |

#### 4.5 Métricas custom del test:
- `test_unique_sent`: Contador de eventos únicos enviados
- `test_duplicates_sent`: Contador de eventos duplicados enviados

**Criterios de éxito**:
- ✅ 1000 eventos enviados (900 únicos + 100 duplicados)
- ✅ 900 registros únicos en tabla `matches`
- ✅ 1000 registros en tabla `processed_events`
- ✅ 0 duplicados en base de datos
- ✅ Efectividad de idempotencia: 90%

**Documentación completa**: [../docs/IDEMPOTENCY_EXPERIMENT.md](../docs/IDEMPOTENCY_EXPERIMENT.md)

---
