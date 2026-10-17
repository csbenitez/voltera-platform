<#
.SYNOPSIS
    Instala la capa de Add-ons del lab EDA.
.DESCRIPTION
    local (k3d, región on-prem): solo KEDA.
    azure (AKS, región nube):    Istio (base + istiod + ingress gateway como borde del
                                 Event Mesh), cert-manager y KEDA.
    Knative es opcional (-Knative): el broker de eventos es Redpanda (deploy/base); Knative Eventing
    solo se usa para el ruteo de declaraciones entre regiones (deploy/knative).
.PARAMETER Target
    "local" o "azure".
.PARAMETER Context
    Contexto de kubectl. Por defecto k3d-voltera-onprem / voltera-dev-aks.
#>
[CmdletBinding()]
param(
    [ValidateSet("local", "azure")]
    [string]$Target = "local",
    [string]$Context = "",
    [switch]$Knative   # instala Knative Eventing (+ eventing-kafka-broker) para el ruteo de declaraciones
)
$ErrorActionPreference = "Stop"

$v = @{ ISTIO_VERSION = "1.24.2"; CERT_MANAGER_VERSION = "v1.16.2"; KEDA_HELM_CHART_VERSION = "2.16.0" }
Get-Content "$PSScriptRoot\versions.env" | ForEach-Object {
    if ($_ -match '^\s*([A-Z0-9_]+)\s*=\s*"?([^"#\s]+)"?') { $v[$matches[1]] = $matches[2] }
}
if (-not $Context) { $Context = if ($Target -eq "azure") { "voltera-dev-aks" } else { "k3d-voltera-onprem" } }

function Invoke-Native { param([string]$Exe, [string[]]$Arguments)
    & $Exe @Arguments
    if ($LASTEXITCODE -ne 0) { throw "$Exe $($Arguments -join ' ') falló ($LASTEXITCODE)" }
}

Write-Host "== Add-ons EDA | target=$Target | context=$Context ==" -ForegroundColor Cyan
Invoke-Native kubectl @("--context", $Context, "cluster-info")

Invoke-Native helm @("repo", "add", "kedacore", "https://kedacore.github.io/charts", "--force-update")
if ($Target -eq "azure") {
    Invoke-Native helm @("repo", "add", "istio", "https://istio-release.storage.googleapis.com/charts", "--force-update")
    Invoke-Native helm @("repo", "add", "jetstack", "https://charts.jetstack.io", "--force-update")
}
Invoke-Native helm @("repo", "update")

if ($Target -eq "azure") {
    $iv = $v.ISTIO_VERSION
    Write-Host "`n[Istio $iv] base + istiod + ingress (edge del mesh)" -ForegroundColor Yellow
    Invoke-Native helm @("--kube-context", $Context, "upgrade", "--install", "istio-base", "istio/base", "-n", "istio-system", "--create-namespace", "--version", $iv)
    Invoke-Native helm @("--kube-context", $Context, "upgrade", "--install", "istiod", "istio/istiod", "-n", "istio-system", "--version", $iv, "--set", "pilot.resources.requests.cpu=50m", "--set", "pilot.resources.requests.memory=256Mi", "--wait")
    Invoke-Native helm @("--kube-context", $Context, "upgrade", "--install", "istio-ingress", "istio/gateway", "-n", "istio-system", "--version", $iv, "-f", "$PSScriptRoot\istio\aks-values.yaml", "--wait")

    $cv = $v.CERT_MANAGER_VERSION
    Write-Host "`n[cert-manager $cv]" -ForegroundColor Yellow
    Invoke-Native helm @("--kube-context", $Context, "upgrade", "--install", "cert-manager", "jetstack/cert-manager", "-n", "cert-manager", "--create-namespace", "--version", $cv, "--set", "crds.enabled=true", "--wait")
}

$kv = $v.KEDA_HELM_CHART_VERSION
Write-Host "`n[KEDA $kv]" -ForegroundColor Yellow
Invoke-Native helm @("--kube-context", $Context, "upgrade", "--install", "keda", "kedacore/keda", "-n", "keda", "--create-namespace", "--version", $kv, "-f", "$PSScriptRoot\keda\values.yaml", "--wait")

if ($Knative) {
    $kn = $v.KNATIVE_VERSION; $kk = $v.KNATIVE_KAFKA_VERSION
    $KN = "https://github.com/knative/eventing/releases/download/knative-v$kn"
    $KK = "https://github.com/knative-extensions/eventing-kafka-broker/releases/download/knative-v$kk"
    Write-Host "`n[Knative Eventing $kn + kafka $kk]" -ForegroundColor Yellow
    Invoke-Native kubectl @("--context", $Context, "apply", "-f", "$KN/eventing-crds.yaml")
    Invoke-Native kubectl @("--context", $Context, "apply", "-f", "$KN/eventing-core.yaml")
    Invoke-Native kubectl @("--context", $Context, "-n", "knative-eventing", "wait", "--for=condition=Available", "deploy", "--all", "--timeout=300s")
    Invoke-Native kubectl @("--context", $Context, "apply", "-f", "$KK/eventing-kafka-controller.yaml")
    if ($Target -eq "azure") {
        Invoke-Native kubectl @("--context", $Context, "apply", "-f", "$KK/eventing-kafka-broker.yaml")
        Invoke-Native kubectl @("--context", $Context, "apply", "-f", "$KK/eventing-kafka-sink.yaml")
    } else {
        Invoke-Native kubectl @("--context", $Context, "apply", "-f", "$KK/eventing-kafka-source.yaml")
    }
    # Trazas de Knative -> otel-collector de la región (OTLP/HTTP) -> App Insights. Desde 1.18 se
    # configuran en config-observability (config-tracing quedó deprecado). Se pasa por archivo para
    # evitar problemas de comillas al invocar kubectl.
    $patchFile = Join-Path ([IO.Path]::GetTempPath()) "knative-observability-patch.json"
    '{"data":{"tracing-protocol":"http/protobuf","tracing-endpoint":"http://otel-collector.voltera.svc.cluster.local:4318/v1/traces","tracing-sampling-rate":"1"}}' |
        Set-Content -Path $patchFile -Encoding utf8NoBOM
    Invoke-Native kubectl @("--context", $Context, "-n", "knative-eventing", "patch", "cm", "config-observability", "--type", "merge", "--patch-file", $patchFile)
    # Los data planes piden 1 CPU y los nodos del lab son chicos (k3d: 2 CPU): bajar requests (los límites no cambian)
    # y apagar lo que el lab no usa para que el pod no quede Pending.
    foreach ($w in (kubectl --context $Context -n knative-eventing get deploy, sts -o name | Where-Object { $_ -match 'kafka-(source|broker|sink)-(dispatcher|receiver)' })) {
        Invoke-Native kubectl @("--context", $Context, "-n", "knative-eventing", "set", "resources", $w, "--requests=cpu=100m,memory=200Mi")
    }
    kubectl --context $Context -n knative-eventing scale deploy/job-sink sts/request-reply --replicas=0 2>$null | Out-Null
}

Write-Host "`nAdd-ons listos en $Target." -ForegroundColor Green
