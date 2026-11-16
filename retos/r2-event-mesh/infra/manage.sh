#!/usr/bin/env bash
# ==============================================================================
# VOLTERA INFRASTRUCTURE MANAGER (Bash CLI)
# ==============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

show_help() {
  echo "================================================================="
  echo "  VOLTERA INFRASTRUCTURE MANAGER (CLI Unificado)                 "
  echo "================================================================="
  echo ""
  echo "Uso: ./infra/manage.sh <Target> <Action> [Opciones]"
  echo ""
  echo "🎯 Entornos (Target):"
  echo "  local       Gestiona el clúster local K3s (k3d en Docker Desktop)"
  echo "  azure       Gestiona el clúster Azure AKS, Terraform y FinOps"
  echo "  addons      Gestiona el stack de addons (local: KEDA; azure: Istio edge + cert-manager + KEDA)"
  echo ""
  echo "⚡ Acciones para 'local':"
  echo "  start       Inicia clúster k3d"
  echo "  stop        Pausa clúster k3d (k3d cluster stop voltera-onprem)"
  echo "  delete      Elimina clúster k3d (k3d cluster delete voltera-onprem)"
  echo "  status      Muestra clústeres k3d y estado kubectl"
  echo "  addons      Instala KEDA en el clúster local"
  echo ""
  echo "⚡ Acciones para 'azure':"
  echo "  status      Consulta el PowerState de AKS"
  echo "  start       Inicia nodos de AKS (az aks start)"
  echo "  stop        Detiene cómputo de AKS (az aks stop)"
  echo "  connect     Descarga credenciales a kubectl"
  echo "  plan        Ejecuta terraform plan en environments/dev"
  echo "  apply       Ejecuta terraform apply en environments/dev"
  echo "  addons      Instala Istio edge, cert-manager y KEDA en AKS"
  echo ""
  echo "💡 Ejemplos:"
  echo "  ./infra/manage.sh local start"
  echo "  ./infra/manage.sh local addons"
  echo "  ./infra/manage.sh azure status"
  echo "  ./infra/manage.sh azure stop"
  echo "================================================================="
}

TARGET="${1:-help}"
ACTION="${2:-status}"
PUBLIC_IP="${3:-}"

RESOURCE_GROUP="rg-voltera-dev"
CLUSTER_NAME="voltera-dev-aks"

case "$TARGET" in
  local)
    case "$ACTION" in
      start)
        echo "Iniciando clúster k3d 'voltera-onprem'..."
        if k3d cluster list -o json 2>/dev/null | grep -q "voltera-onprem"; then
          k3d cluster start voltera-onprem
        else
          k3d cluster create voltera-onprem \
            --api-port 6550 \
            -p "8090:80@loadbalancer" \
            -p "8443:443@loadbalancer" \
            --k3s-arg "--disable=traefik@server:0" \
            --wait
        fi
        kubectl config use-context k3d-voltera-onprem
        ;;
      stop)
        echo "Deteniendo clúster k3d 'voltera-onprem'..."
        k3d cluster stop voltera-onprem
        ;;
      delete)
        echo "Eliminando clúster k3d 'voltera-onprem'..."
        k3d cluster delete voltera-onprem
        ;;
      status)
        k3d cluster list
        kubectl cluster-info
        ;;
      addons)
        "$SCRIPT_DIR/addons/install.sh" --target local
        ;;
      *)
        echo "Acción no reconocida '$ACTION' para target local."
        ;;
    esac
    ;;

  azure)
    case "$ACTION" in
      status)
        az aks show --resource-group "$RESOURCE_GROUP" --name "$CLUSTER_NAME" \
          --query "{powerState:powerState.code, provisioningState:provisioningState}" -o table
        ;;
      start)
        echo "Iniciando clúster AKS $CLUSTER_NAME..."
        az aks start --resource-group "$RESOURCE_GROUP" --name "$CLUSTER_NAME"
        ;;
      stop)
        echo "Deteniendo clúster AKS $CLUSTER_NAME..."
        az aks stop --resource-group "$RESOURCE_GROUP" --name "$CLUSTER_NAME"
        ;;
      connect)
        az aks get-credentials --resource-group "$RESOURCE_GROUP" --name "$CLUSTER_NAME" --overwrite-existing
        kubectl get nodes
        ;;
      plan)
        (cd "$SCRIPT_DIR/azure-terraform/environments/dev" && terraform plan)
        ;;
      apply)
        (cd "$SCRIPT_DIR/azure-terraform/environments/dev" && terraform apply)
        ;;
      addons)
        "$SCRIPT_DIR/addons/install.sh" --target azure
        ;;
      *)
        echo "Acción no reconocida '$ACTION' para target azure."
        ;;
    esac
    ;;

  addons)
    "$SCRIPT_DIR/addons/install.sh" --target "$ACTION"
    ;;

  help|*)
    show_help
    ;;
esac
