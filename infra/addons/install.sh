#!/usr/bin/env bash
# Instala la capa de Add-ons del lab EDA.
#   local (k3d, on-prem): KEDA
#   azure (AKS, nube):    Istio (edge del Event Mesh) + cert-manager + KEDA
# Uso: ./install.sh --target local|azure [--context <kubectl-context>]
set -euo pipefail
DIR="$(cd "$(dirname "$0")" && pwd)"
source "$DIR/versions.env"

TARGET=local; CONTEXT=""; KNATIVE=0
while [[ $# -gt 0 ]]; do
  case "$1" in
    --target) TARGET="$2"; shift 2 ;;
    --context) CONTEXT="$2"; shift 2 ;;
    --knative) KNATIVE=1; shift ;;
    *) echo "argumento desconocido: $1" >&2; exit 1 ;;
  esac
done
[[ -z "$CONTEXT" ]] && { [[ "$TARGET" == azure ]] && CONTEXT=voltera-dev-aks || CONTEXT=k3d-voltera-onprem; }
H=(helm --kube-context "$CONTEXT")

echo "== Add-ons EDA | target=$TARGET | context=$CONTEXT =="
kubectl --context "$CONTEXT" cluster-info >/dev/null

helm repo add kedacore https://kedacore.github.io/charts --force-update
if [[ "$TARGET" == azure ]]; then
  helm repo add istio https://istio-release.storage.googleapis.com/charts --force-update
  helm repo add jetstack https://charts.jetstack.io --force-update
fi
helm repo update

if [[ "$TARGET" == azure ]]; then
  "${H[@]}" upgrade --install istio-base istio/base -n istio-system --create-namespace --version "$ISTIO_VERSION"
  "${H[@]}" upgrade --install istiod istio/istiod -n istio-system --version "$ISTIO_VERSION" \
    --set pilot.resources.requests.cpu=50m --set pilot.resources.requests.memory=256Mi --wait
  "${H[@]}" upgrade --install istio-ingress istio/gateway -n istio-system --version "$ISTIO_VERSION" \
    -f "$DIR/istio/aks-values.yaml" --wait
  "${H[@]}" upgrade --install cert-manager jetstack/cert-manager -n cert-manager --create-namespace \
    --version "$CERT_MANAGER_VERSION" --set crds.enabled=true --wait
fi

"${H[@]}" upgrade --install keda kedacore/keda -n keda --create-namespace \
  --version "$KEDA_HELM_CHART_VERSION" -f "$DIR/keda/values.yaml" --wait
if [[ "$KNATIVE" == 1 ]]; then
  KN="https://github.com/knative/eventing/releases/download/knative-v$KNATIVE_VERSION"
  KK="https://github.com/knative-extensions/eventing-kafka-broker/releases/download/knative-v$KNATIVE_KAFKA_VERSION"
  KC=(kubectl --context "$CONTEXT")
  echo "== Knative Eventing $KNATIVE_VERSION (+ kafka $KNATIVE_KAFKA_VERSION) =="
  "${KC[@]}" apply -f "$KN/eventing-crds.yaml"
  "${KC[@]}" apply -f "$KN/eventing-core.yaml"
  "${KC[@]}" -n knative-eventing wait --for=condition=Available deploy --all --timeout=300s
  "${KC[@]}" apply -f "$KK/eventing-kafka-controller.yaml"
  if [[ "$TARGET" == azure ]]; then
    "${KC[@]}" apply -f "$KK/eventing-kafka-broker.yaml"
    "${KC[@]}" apply -f "$KK/eventing-kafka-sink.yaml"
  else
    "${KC[@]}" apply -f "$KK/eventing-kafka-source.yaml"
  fi
  # Los spans de Knative llegan al otel-collector de la región (receptor zipkin) y de ahí a App Insights.
  "${KC[@]}" -n knative-eventing patch cm config-tracing --type merge -p     '{"data":{"backend":"zipkin","zipkin-endpoint":"http://otel-collector.voltera.svc.cluster.local:9411/api/v2/spans","sample-rate":"1"}}'
  "${KC[@]}" -n knative-eventing get pods
fi
echo "Add-ons listos en $TARGET."
