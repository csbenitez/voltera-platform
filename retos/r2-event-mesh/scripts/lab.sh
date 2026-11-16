#!/usr/bin/env bash
# CLI del lab EDA de Voltera (equivalente POSIX de scripts/lab.ps1).
#   ./scripts/lab.sh <comando> [onprem|cloud|all] [opciones]
# Comandos: cleanup | addons | build | secrets | deploy | up | inject | dashboard | status |
#           failover-test | knative-test | knative-kql | trace | logs | aks <start|stop|status> | down
# Opciones por variables de entorno: KNATIVE=1 (addons) FOREIGN=0.2 (inject) N=100 RATE=50 TAG=... TRACE_ID=... SERVICE=matching-engine FORCE=1
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
STATE="$ROOT/.lab"; mkdir -p "$STATE"
TF_DIR="$ROOT/infra/azure-terraform/environments/dev"
RG=rg-voltera-dev; AKS=voltera-dev-aks; K3D=voltera-onprem; NS=voltera
LOCAL_IMAGE=voltera/voltera-eda
N="${N:-100}"; RATE="${RATE:-50}"; FOREIGN="${FOREIGN:-0}"

CMD="${1:-help}"; TARGET="${2:-all}"
ctx() { [[ "$1" == cloud ]] && echo voltera-dev-aks || echo k3d-voltera-onprem; }
k() { local t="$1"; shift; kubectl --context "$(ctx "$t")" "$@"; }
targets() { [[ "$TARGET" == all ]] && echo "onprem cloud" || echo "$TARGET"; }
step() { printf '\n\033[36m==> %s\033[0m\n' "$*"; }
confirm() { [[ "${FORCE:-}" == 1 ]] && return; read -r -p "$1 [y/N] " a; [[ "$a" =~ ^(y|s|yes|si)$ ]] || { echo "Cancelado"; exit 1; }; }

tag() {
  if [[ -n "${TAG:-}" ]]; then echo "$TAG"; return; fi
  [[ -f "$STATE/image-tag" ]] || { echo "No hay imagen: ejecuta '$0 build'" >&2; exit 1; }
  cat "$STATE/image-tag"
}
acr() {
  if [[ -f "$STATE/acr" ]]; then cat "$STATE/acr"; return; fi
  local l; l="$(terraform -chdir="$TF_DIR" output -raw acr_login_server 2>/dev/null || true)"
  [[ -z "$l" ]] && l="$(az acr list -g "$RG" --query '[0].loginServer' -o tsv)"
  [[ -z "$l" ]] && { echo "ACR no encontrado (terraform apply)" >&2; exit 1; }
  echo "$l" | tee "$STATE/acr"
}
image() { [[ "$1" == cloud ]] && echo "$(acr)/voltera/voltera-eda:$(tag)" || echo "$LOCAL_IMAGE:$(tag)"; }
svc_json() { k cloud get --raw "/api/v1/namespaces/$NS/services/$1:80/proxy$2" 2>/dev/null || true; }
mesh_state() { svc_json mesh-router /status | sed -n 's/.*"state": "\([A-Z_]*\)".*/\1/p' | head -1; }

cleanup() {
  for t in $(targets); do
    step "Limpieza de la arquitectura anterior en $t"
    confirm "¿Eliminar Knative, servicios viejos (billing/payment/ami-mock) y extras en $t?"
    k "$t" delete ksvc --all -n default --ignore-not-found 2>/dev/null || true
    k "$t" delete ns knative-serving --ignore-not-found --wait=false   # Eventing se conserva (deploy/knative)
    k "$t" get validatingwebhookconfiguration,mutatingwebhookconfiguration -o name | grep 'knative' | grep -v 'eventing\|kafka' | xargs -r kubectl --context "$(ctx "$t")" delete --ignore-not-found
    k "$t" get routes.serving.knative.dev,services.serving.knative.dev -A \
      -o jsonpath='{range .items[*]}{.kind} {.metadata.name} {.metadata.namespace}{"\n"}{end}' 2>/dev/null |
      while read -r kind name rns; do [[ -n "$name" ]] && k "$t" -n "$rns" patch "${kind,,}/$name" --type=merge -p '{"metadata":{"finalizers":[]}}' >/dev/null || true; done
    k "$t" get crd -o name | grep 'serving\.knative\.dev\|networking\.internal\.knative\.dev\|caching\.internal\.knative\.dev' | xargs -r kubectl --context "$(ctx "$t")" delete --ignore-not-found --wait=false
    k "$t" delete svc knative-local-gateway -n istio-system --ignore-not-found
    if [[ "$t" == onprem ]]; then
      k "$t" delete ns voltera-onprem --ignore-not-found --wait=false
      for r in istio-ingress istiod istio-base; do helm --kube-context "$(ctx "$t")" uninstall "$r" -n istio-system 2>/dev/null || true; done
      k "$t" delete ns istio-system --ignore-not-found --wait=false
      k "$t" get crd -o name | grep 'istio\.io' | xargs -r kubectl --context "$(ctx "$t")" delete --ignore-not-found --wait=false
      k "$t" delete apiservice v1beta1.external.metrics.k8s.io --ignore-not-found
      for _ in $(seq 30); do k "$t" get ns keda >/dev/null 2>&1 || break; sleep 2; done
      "$ROOT/infra/addons/install.sh" --target local
    else
      k "$t" delete ns "$NS" --ignore-not-found
      k "$t" -n istio-system delete deploy,svc grafana kiali prometheus otel-collector --ignore-not-found
      k "$t" -n istio-system delete cm grafana kiali prometheus otel-collector-conf istio-grafana-dashboards istio-services-grafana-dashboards --ignore-not-found
      k "$t" -n istio-system delete sa grafana kiali prometheus --ignore-not-found
      k "$t" -n istio-system delete secret appinsights-secret --ignore-not-found
      k "$t" -n istio-system delete telemetry mesh-default-tracing --ignore-not-found 2>/dev/null || true
      k "$t" delete clusterrole,clusterrolebinding kiali prometheus --ignore-not-found
    fi
  done
}

addons() { for t in $(targets); do "$ROOT/infra/addons/install.sh" --target "$([[ $t == cloud ]] && echo azure || echo local)" $([[ "${KNATIVE:-}" == 1 ]] && echo --knative); done; }

build() {
  local tg="${TAG:-$(date +%Y%m%d-%H%M%S)}"
  step "docker build $LOCAL_IMAGE:$tg"
  docker build -t "$LOCAL_IMAGE:$tg" "$ROOT/services"
  echo "$tg" > "$STATE/image-tag"
  for t in $(targets); do
    if [[ "$t" == onprem ]]; then
      k3d image import "$LOCAL_IMAGE:$tg" -c "$K3D"
    else
      local a; a="$(acr)"
      az acr login -n "${a%%.*}"
      docker tag "$LOCAL_IMAGE:$tg" "$a/voltera/voltera-eda:$tg"
      docker push "$a/voltera/voltera-eda:$tg"
    fi
  done
}

secrets() {
  [[ -f "$STATE/mesh-token" ]] || openssl rand -hex 32 > "$STATE/mesh-token"
  local tok cs; tok="$(cat "$STATE/mesh-token")"
  cs="$(terraform -chdir="$TF_DIR" output -raw app_insights_connection_string 2>/dev/null || true)"
  [[ "$cs" == *InstrumentationKey=* ]] || cs="$(az resource show -g "$RG" -n voltera-dev-appinsights --resource-type Microsoft.Insights/components --query properties.ConnectionString -o tsv)"
  for t in $(targets); do
    step "Secrets en $t"
    k "$t" create ns "$NS" --dry-run=client -o yaml | k "$t" apply -f -
    k "$t" -n "$NS" create secret generic mesh-token --from-literal=token="$tok" --dry-run=client -o yaml | k "$t" apply -f -
    k "$t" -n "$NS" create secret generic appinsights-secret --from-literal=connection-string="$cs" --dry-run=client -o yaml | k "$t" apply -f -
  done
}

render() {
  local out; out="$(kubectl kustomize "$ROOT/deploy/$1" | sed "s#__TAG__#$(tag)#g")"
  [[ "$1" == cloud ]] && out="$(echo "$out" | sed "s#__ACR__#$(acr)#g")"
  echo "$out"
}

deploy() {
  for t in $(targets); do
    step "Deploy $t (tag $(tag))"
    [[ "$t" == cloud ]] && k "$t" apply -f "$ROOT/deploy/cloud-edge"
    k "$t" -n "$NS" delete job redpanda-topics --ignore-not-found
    render "$t" | k "$t" apply -f -
    k "$t" -n "$NS" rollout status statefulset/redpanda --timeout=180s
    k "$t" -n "$NS" wait --for=condition=complete job/redpanda-topics --timeout=180s
    k "$t" -n "$NS" rollout restart deploy -l app.kubernetes.io/part-of=voltera-eda
    for d in $(k "$t" -n "$NS" get deploy -l app.kubernetes.io/part-of=voltera-eda -o name | grep -v declaration-gateway); do
      k "$t" -n "$NS" rollout status "$d" --timeout=120s
    done
  done
  deploy_knative
}

inject() {
  for t in $(targets); do
    local stamp job; stamp="$(date +%H%M%S)"; job="injector-$stamp"
    step "Inyectando $N declaraciones en $t -> job/$job"
    sed -e "s#__STAMP__#$stamp#" -e "s#__N__#$N#" -e "s#__RATE__#$RATE#" -e "s#__FOREIGN__#$FOREIGN#" -e "s#__IMAGE__#$(image "$t")#" \
      "$ROOT/deploy/jobs/injector.yaml" | k "$t" apply -f -
    k "$t" -n "$NS" wait --for=condition=complete "job/$job" --timeout=300s
    k "$t" -n "$NS" logs "job/$job" | grep -v '^{' | tee "$STATE/last-inject.log"
    grep -Eo '^\s+[0-9a-f]{32}$' "$STATE/last-inject.log" | head -1 | tr -d ' ' > "$STATE/last-trace" || true
  done
}

status() {
  for t in $(targets); do
    step "Región $t"
    k "$t" -n "$NS" get pods,scaledobject 2>/dev/null || true
    k "$t" -n "$NS" exec redpanda-0 -c redpanda -- rpk group describe declaration-gateway matching-engine 2>/dev/null | grep -E 'GROUP|TOTAL-LAG|STATE' || true
    if [[ "$t" == cloud ]]; then
      echo "--- Event Mesh"; svc_json mesh-router /status
      echo "--- Facturación"; svc_json facturacion /stats
      k "$t" -n "$NS" get broker,trigger,kafkasink 2>/dev/null || true
    else
      k "$t" -n "$NS" get kafkasource 2>/dev/null || true
      k "$t" -n "$NS" exec redpanda-0 -c redpanda -- rpk group describe knative-declaration-forward 2>/dev/null | grep -E 'GROUP|TOTAL-LAG|STATE' || true
    fi
  done
}

wait_state() { for _ in $(seq "$2"); do [[ "$(mesh_state)" == "$1" ]] && return 0; sleep 2; done; return 1; }
failover_count() { svc_json facturacion /stats | sed -n 's/.*"onprem->cloud": \([0-9]*\).*/\1/p' | head -1; }

failover_test() {
  TARGET=onprem
  step "Precondición: mesh en NORMAL"
  k onprem -n "$NS" scale deploy/matching-engine --replicas=1
  wait_state NORMAL 45 || { echo "Mesh no está en NORMAL"; exit 1; }
  local before; before="$(failover_count)"; before="${before:-0}"
  step "Apagando matching-engine local"
  k onprem -n "$NS" scale deploy/matching-engine --replicas=0
  inject
  step "Esperando FAILOVER_ACTIVE"
  wait_state FAILOVER_ACTIVE 45 || { echo "FAIL: el mesh no entró en failover"; exit 1; }
  local after="$before"
  for _ in $(seq 30); do sleep 3; after="$(failover_count)"; after="${after:-0}"; (( after > before )) && break; done
  step "Restaurando matching-engine local"
  k onprem -n "$NS" scale deploy/matching-engine --replicas=1
  wait_state NORMAL 60 && back=1 || back=0
  svc_json mesh-router /status
  if (( after > before && back == 1 )); then echo "PASS: $((after - before)) calces onprem->cloud; mesh volvió a NORMAL"
  else echo "FAIL: nuevos=$((after - before)) normal=$back"; exit 1; fi
}


knative_kql() {
  cat <<'KQL'
// 1) Vista general del canal Knative: quién envió, quién recibió y en qué región
union dependencies, requests
| where timestamp > ago(2h)
| extend ch = tostring(customDimensions["voltera.channel"]), fwd = tostring(customDimensions["voltera.forwarded_from"]),
         msg = tostring(customDimensions["messaging.message.id"])
| where ch == "knative" or cloud_RoleName startswith "knative" or name startswith "knative forward"
| project timestamp, cloud_RoleName, name, itemType, duration, fwd, msg, operation_Id
| order by timestamp asc

// 2) Una declaración de punta a punta (ambas trazas) por su ce-id, aunque Knative corte el traceparent
let id = "<ce-id>";
union dependencies, requests
| where timestamp > ago(2h)
| where tostring(customDimensions["messaging.message.id"]) == id
| project timestamp, cloud_RoleName, name, duration, operation_Id, region = tostring(customDimensions["voltera.origin_region"])
| order by timestamp asc

// 3) ¿Se cortó el traceparent? Una declaración reenviada con DOS operation_Id distintos = traza partida
union dependencies, requests
| where timestamp > ago(2h) and tostring(customDimensions["messaging.message.id"]) != ""
| summarize trazas = dcount(operation_Id), roles = make_set(cloud_RoleName) by msg = tostring(customDimensions["messaging.message.id"])
| where array_length(roles) > 3
| summarize unidas = countif(trazas == 1), partidas = countif(trazas > 1)
KQL
}

hw() { # suma de high-watermark de un topic: hw <ctx-target> <topic>
  k "$1" -n "$NS" exec redpanda-0 -c redpanda -- rpk topic describe "$2" -p 2>/dev/null |
    awk 'NR==1{for(i=1;i<=NF;i++) if($i=="HIGH-WATERMARK") c=i; next} c&&$1~/^[0-9]+$/{s+=$c} END{print s+0}'
}

deploy_knative() {
  for t in $(targets); do
    local crd=kafkasources.sources.knative.dev; [[ "$t" == cloud ]] && crd=kafkasinks.eventing.knative.dev
    if ! k "$t" get crd "$crd" >/dev/null 2>&1; then echo "Knative no instalado en $t (addons con KNATIVE=1); se omite deploy/knative"; continue; fi
    step "Knative en $t"
    local tok; tok="$(cat "$STATE/mesh-token")"
    [[ "$t" == cloud ]] && { kubectl kustomize "$ROOT/deploy/knative/cloud-edge" | sed "s#__MESH_TOKEN__#$tok#g" | k "$t" apply -f -; }
    kubectl kustomize "$ROOT/deploy/knative/$t" | sed "s#__MESH_TOKEN__#$tok#g" | k "$t" apply -f -
  done
}

knative_test() {
  TARGET=onprem; FOREIGN="${FOREIGN:-0.2}"
  step "Línea base (high-watermark de los topics)"
  local f0 i0 d0; f0="$(hw onprem voltera.declaration.forward.v1)"; i0="$(hw cloud voltera.ingress.declarations)"; d0="$(hw cloud voltera.declaration.dlq.v1)"
  echo "forward(onprem)=$f0 ingress(nube)=$i0 dlq(nube)=$d0"
  inject
  step "Esperando que Knative entregue en la nube (hasta 90 s)"
  local f1 i1 d1 df di dd
  for _ in $(seq 30); do
    sleep 3; f1="$(hw onprem voltera.declaration.forward.v1)"; i1="$(hw cloud voltera.ingress.declarations)"
    df=$((f1 - f0)); di=$((i1 - i0)); (( df > 0 && di >= df )) && break
  done
  d1="$(hw cloud voltera.declaration.dlq.v1)"; dd=$((d1 - d0))
  echo "reenviadas por el gateway onprem: $df | llegadas a la ingesta de la nube: $di | DLQ: $dd"
  if (( df > 0 && di == df && dd == 0 )); then echo "PASS: $df declaraciones ajenas cruzaron onprem -> nube vía Knative, sin pérdida ni DLQ"
  else echo "FAIL (si la nube también recibe inyección propia, di puede exceder df)"; fi
  step "Ver en Application Insights (Logs)"; knative_kql
}

trace() {
  local id="${TRACE_ID:-$(cat "$STATE/last-trace" 2>/dev/null || true)}"
  [[ -z "$id" ]] && { echo "Indica TRACE_ID=<id>"; exit 1; }
  local q="union requests, dependencies | where timestamp > ago(2h) | where operation_Id == '$id' | project timestamp, cloud_RoleName, name, itemType, duration, success | order by timestamp asc"
  echo "$q"
  az monitor app-insights query --app voltera-dev-appinsights -g "$RG" --analytics-query "$q" -o table 2>/dev/null \
    || echo "(az extension add -n application-insights para ejecutarla aquí; o pégala en App Insights > Logs)"
}

case "$CMD" in
  cleanup) cleanup ;;
  addons) addons ;;
  build) build ;;
  secrets) secrets ;;
  deploy) deploy ;;
  up) build; secrets; deploy ;;
  inject) inject ;;
  dashboard)
    k onprem -n "$NS" rollout status deploy/injector-dashboard --timeout=120s
    step "Dashboard del injector -> http://localhost:${DASH_PORT:-18090} (Ctrl+C para cerrar)"
    k onprem -n "$NS" port-forward svc/injector-dashboard ${DASH_PORT:-18090}:80 ;;
  status) status ;;
  failover-test) failover_test ;;
  knative-test) knative_test ;;
  knative-kql) knative_kql ;;
  trace) trace ;;
  logs) t="$TARGET"; [[ "$t" == all ]] && t=onprem; k "$t" -n "$NS" logs -f "deploy/${SERVICE:-matching-engine}" --tail=50 ;;
  aks)
    case "${2:-status}" in
      start) az aks start -g "$RG" -n "$AKS" ;;
      stop) az aks stop -g "$RG" -n "$AKS" --no-wait ;;
      *) az aks show -g "$RG" -n "$AKS" --query '{power:powerState.code,state:provisioningState}' -o table ;;
    esac ;;
  down) for t in $(targets); do confirm "¿Borrar ns $NS en $t?"; k "$t" delete ns "$NS" --ignore-not-found; done ;;
  *) sed -n '2,8p' "$0" ;;
esac
