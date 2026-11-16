<#
.SYNOPSIS
    CLI del lab EDA de Voltera (ver docs/ARCH.md y docs/LAB_RUNBOOK.md).
.DESCRIPTION
    Región 1 = k3d "voltera-onprem" (on-prem simulado)  -> contexto k3d-voltera-onprem
    Región 2 = AKS "voltera-dev-aks" (nube)              -> contexto voltera-dev-aks

    Comandos:
      cleanup        Elimina restos de la arquitectura anterior (Knative, billing/payment, ami-mock,
                     samples de Istio, Istio local) y desbloquea KEDA. Pide confirmación (-Force la omite).
      addons         Instala add-ons (local: KEDA | nube: Istio edge + cert-manager + KEDA).
      build          Construye la imagen única voltera-eda; la importa en k3d y/o la sube a ACR.
      secrets        Crea mesh-token (compartido) y appinsights-secret en el/los clusters.
      deploy         Aplica deploy/<onprem|cloud> (kustomize) con el tag construido.
      up             build + secrets + deploy.
      inject         Lanza el Job injector con -N declaraciones a -Rate msg/s.
      dashboard      Abre el dashboard de carga del injector (port-forward a onprem, http://localhost:18090).
      status         Pods, KEDA, lag de Redpanda, estado del Event Mesh y facturación.
      knative-test   Inyecta declaraciones de medidores ajenos (-Foreign) y verifica el cruce onprem -> nube por Knative.
      knative-kql    Imprime las consultas KQL para ver el canal Knative en Application Insights.
      failover-test  Apaga el matching-engine local, inyecta, verifica que el mesh enruta a la nube y restaura.
      trace          Muestra la consulta KQL (y la ejecuta si hay extensión az) para un trace id.
      logs           Sigue los logs de -Service en -Target.
      aks            start | stop | status del cómputo AKS (FinOps). Usa -Action.
      down           Borra el namespace voltera (deja add-ons).
.EXAMPLE
    .\scripts\lab.ps1 up -Target all
    .\scripts\lab.ps1 inject -Target onprem -N 200
    .\scripts\lab.ps1 failover-test
    .\scripts\lab.ps1 aks -Action stop
#>
[CmdletBinding()]
param(
    [Parameter(Position = 0)]
    [ValidateSet("cleanup", "addons", "build", "secrets", "deploy", "up", "inject", "dashboard", "status",
        "failover-test", "knative-test", "knative-kql", "trace", "logs", "aks", "down", "help")]
    [string]$Command = "help",

    [ValidateSet("onprem", "cloud", "all")]
    [string]$Target = "all",

    [int]$N = 100,
    [int]$Rate = 50,
    [double]$Foreign = 0,      # fracción de medidores de la otra región (ruteo Knative)
    [int]$DashPort = 18090,    # puerto local del dashboard del injector
    [switch]$Knative,          # en addons: instala Knative Eventing
    [string]$Tag = "",
    [string]$TraceId = "",
    [string]$Service = "matching-engine",
    [ValidateSet("start", "stop", "status")]
    [string]$Action = "status",
    [switch]$Force
)

$ErrorActionPreference = "Stop"
$Root = Resolve-Path "$PSScriptRoot\.."
$StateDir = Join-Path $Root ".lab"
$TfDir = Join-Path $Root "infra\azure-terraform\environments\dev"
$ResourceGroup = "rg-voltera-dev"
$AksName = "voltera-dev-aks"
$K3dCluster = "voltera-onprem"
$Ns = "voltera"
$Ctx = @{ onprem = "k3d-voltera-onprem"; cloud = "voltera-dev-aks" }
$LocalImage = "voltera/voltera-eda"
New-Item -ItemType Directory -Force $StateDir | Out-Null

# ----------------------------------------------------------------- helpers
function Step($msg) { Write-Host "`n==> $msg" -ForegroundColor Cyan }
function Info($msg) { Write-Host "    $msg" -ForegroundColor Gray }
function Ok($msg) { Write-Host "    $msg" -ForegroundColor Green }
function Warn($msg) { Write-Host "    $msg" -ForegroundColor Yellow }

function Invoke-Native {
    param([string]$Exe, [string[]]$Arguments, [switch]$AllowFail)
    & $Exe @Arguments
    if ($LASTEXITCODE -ne 0 -and -not $AllowFail) { throw "$Exe $($Arguments -join ' ') falló ($LASTEXITCODE)" }
}
# K <onprem|cloud> <args kubectl...>  (sin param block: los flags pasan tal cual; admite pipeline)
function K {
    $t, $rest = $args
    if ($MyInvocation.ExpectingInput) { $input | & kubectl --context $Ctx[$t] @rest }
    else { & kubectl --context $Ctx[$t] @rest }
}
function Targets { if ($Target -eq "all") { @("onprem", "cloud") } else { @($Target) } }

function Get-Tag {
    if ($Tag) { return $Tag }
    $f = Join-Path $StateDir "image-tag"
    if (Test-Path $f) { return (Get-Content $f -Raw).Trim() }
    throw "No hay imagen construida. Ejecuta: .\scripts\lab.ps1 build"
}

function Get-Acr {
    $f = Join-Path $StateDir "acr"
    if (Test-Path $f) { return (Get-Content $f -Raw).Trim() }
    $login = ""
    try { $login = (& terraform "-chdir=$TfDir" output -raw acr_login_server 2>$null) } catch {}
    if (-not $login -or $LASTEXITCODE -ne 0) {
        $login = (& az acr list -g $ResourceGroup --query "[0].loginServer" -o tsv 2>$null)
    }
    if (-not $login) { throw "No se encontró ACR. Ejecuta 'terraform apply' en infra/azure-terraform/environments/dev." }
    $login.Trim() | Set-Content $f
    return $login.Trim()
}

function Get-Image([string]$T) {
    $tag = Get-Tag
    if ($T -eq "cloud") { return "$(Get-Acr)/voltera/voltera-eda:$tag" }
    return "${LocalImage}:$tag"
}

# Llama un endpoint HTTP interno vía el proxy del API server (sin port-forward ni curl en pods distroless).
function Get-SvcJson([string]$T, [string]$Svc, [string]$Path) {
    $raw = K $T get --raw "/api/v1/namespaces/$Ns/services/${Svc}:80/proxy$Path" 2>$null
    if ($LASTEXITCODE -ne 0 -or -not $raw) { return $null }
    return ($raw | Out-String | ConvertFrom-Json)
}

function Confirm-Action([string]$msg) {
    if ($Force) { return }
    $ans = Read-Host "$msg [y/N]"
    if ($ans -notmatch '^(y|s|yes|si)$') { throw "Cancelado por el usuario." }
}

# ----------------------------------------------------------------- comandos
function Invoke-Cleanup {
    foreach ($t in Targets) {
        Step "Limpieza de la arquitectura anterior en $t ($($Ctx[$t]))"
        if ($t -eq "onprem") {
            Info "Se eliminará: ns voltera-onprem (ami-mock, onprem-broker, trigger-to-cloud), ksvc/broker en default,"
            Info "Knative Serving/Eventing + CRDs, Istio (helm) + CRDs. Se desbloquea y reinstala KEDA."
        } else {
            Info "Se eliminará: ns voltera (billing, payment, cloud-event-logger, cloud-broker, triggers, gateway viejo),"
            Info "Knative Serving/Eventing + CRDs, samples grafana/prometheus/kiali, otel-collector viejo y Telemetry de istio-system."
            Info "Se conserva: istiod, istio-ingress (IP/DNS voltera-dev-mesh), cert-manager + certificado, KEDA."
        }
        Confirm-Action "¿Continuar con la limpieza de $t?"

        # Knative (ambos)
        K $t delete ksvc --all -n default --ignore-not-found 2>$null | Out-Null
        # Knative Eventing se conserva: lo usa el ruteo de declaraciones (deploy\knative). Solo se retira Serving.
        K $t delete ns knative-serving --ignore-not-found --wait=false | Out-Null
        $hooks = K $t get "validatingwebhookconfiguration,mutatingwebhookconfiguration" -o name 2>$null | Where-Object { $_ -match "knative" -and $_ -notmatch "eventing|kafka" }
        if ($hooks) { K $t delete @hooks --ignore-not-found | Out-Null }
        # Sin controladores de Knative los finalizers de routes/ksvc bloquean el borrado de CRDs
        foreach ($r in (K $t get "routes.serving.knative.dev,services.serving.knative.dev" -A -o jsonpath='{range .items[*]}{.kind}/{.metadata.name} -n {.metadata.namespace}{"\n"}{end}' 2>$null)) {
            if (-not $r) { continue }
            $kind, $null, $rns = $r -split ' '
            K $t patch $kind.ToLower() -n $rns --type=merge -p '{\"metadata\":{\"finalizers\":[]}}' 2>$null | Out-Null
        }
        $crds = K $t get crd -o name 2>$null | Where-Object { $_ -match "serving\.knative\.dev|networking\.internal\.knative\.dev|caching\.internal\.knative\.dev" }
        if ($crds) { K $t delete @crds --ignore-not-found --wait=false | Out-Null }
        K $t delete svc knative-local-gateway -n istio-system --ignore-not-found | Out-Null
        K $t delete gateway knative-ingress-gateway knative-local-gateway -n knative-serving --ignore-not-found 2>$null | Out-Null

        if ($t -eq "onprem") {
            K $t delete ns voltera-onprem --ignore-not-found --wait=false | Out-Null
            # Istio no se usa en la región local
            foreach ($rel in "istio-ingress", "istiod", "istio-base") {
                & helm --kube-context $Ctx[$t] uninstall $rel -n istio-system 2>$null | Out-Null
            }
            K $t delete ns istio-system --ignore-not-found --wait=false | Out-Null
            $icrds = K $t get crd -o name 2>$null | Where-Object { $_ -match "istio\.io" }
            if ($icrds) { K $t delete @icrds --ignore-not-found --wait=false | Out-Null }
            # KEDA atascado en Terminating por APIService huérfano de métricas externas
            K $t delete apiservice v1beta1.external.metrics.k8s.io --ignore-not-found | Out-Null
            Info "Esperando a que el ns keda termine de borrarse..."
            for ($i = 0; $i -lt 30; $i++) {
                K $t get ns keda 2>$null | Out-Null
                if ($LASTEXITCODE -ne 0) { break }
                Start-Sleep 2
            }
        } else {
            K $t delete ns $Ns --ignore-not-found --wait=true | Out-Null
            K $t -n istio-system delete deploy grafana kiali prometheus otel-collector --ignore-not-found | Out-Null
            K $t -n istio-system delete svc grafana kiali prometheus otel-collector --ignore-not-found | Out-Null
            K $t -n istio-system delete cm grafana kiali prometheus otel-collector-conf istio-grafana-dashboards istio-services-grafana-dashboards --ignore-not-found | Out-Null
            K $t -n istio-system delete sa grafana kiali prometheus --ignore-not-found | Out-Null
            K $t -n istio-system delete secret appinsights-secret --ignore-not-found | Out-Null
            K $t -n istio-system delete telemetry mesh-default-tracing --ignore-not-found 2>$null | Out-Null
            K $t delete clusterrole kiali prometheus --ignore-not-found | Out-Null
            K $t delete clusterrolebinding kiali prometheus --ignore-not-found | Out-Null
        }
        Ok "Limpieza de $t completada."
    }
    if ((Targets) -contains "onprem") {
        Step "Reinstalando KEDA en onprem"
        & "$Root\infra\addons\install.ps1" -Target local
    }
}

function Invoke-Addons {
    foreach ($t in Targets) {
        $tt = if ($t -eq "cloud") { "azure" } else { "local" }
        if ($Knative) { & "$Root\infra\addons\install.ps1" -Target $tt -Context $Ctx[$t] -Knative }
        else { & "$Root\infra\addons\install.ps1" -Target $tt -Context $Ctx[$t] }
    }
}

function Invoke-Build {
    $tag = if ($Tag) { $Tag } else { Get-Date -Format "yyyyMMdd-HHmmss" }
    Step "docker build $LocalImage`:$tag"
    Invoke-Native docker @("build", "-t", "${LocalImage}:$tag", "$Root\services")
    $tag | Set-Content (Join-Path $StateDir "image-tag")
    foreach ($t in Targets) {
        if ($t -eq "onprem") {
            Step "k3d image import -> $K3dCluster"
            Invoke-Native k3d @("image", "import", "${LocalImage}:$tag", "-c", $K3dCluster)
        } else {
            $acr = Get-Acr
            Step "push -> $acr"
            Invoke-Native az @("acr", "login", "-n", ($acr -split '\.')[0])
            Invoke-Native docker @("tag", "${LocalImage}:$tag", "$acr/voltera/voltera-eda:$tag")
            Invoke-Native docker @("push", "$acr/voltera/voltera-eda:$tag")
        }
    }
    Ok "Imagen lista con tag $tag"
}

function Invoke-Secrets {
    $tokFile = Join-Path $StateDir "mesh-token"
    if (-not (Test-Path $tokFile)) {
        $bytes = New-Object byte[] 32
        [System.Security.Cryptography.RandomNumberGenerator]::Fill($bytes)
        [Convert]::ToHexString($bytes).ToLower() | Set-Content $tokFile
    }
    $token = (Get-Content $tokFile -Raw).Trim()

    $cs = ""
    try { $cs = (& terraform "-chdir=$TfDir" output -raw app_insights_connection_string 2>$null) } catch {}
    if (-not $cs -or $cs -notmatch "InstrumentationKey=") {
        $cs = (& az resource show -g $ResourceGroup -n "voltera-dev-appinsights" --resource-type "Microsoft.Insights/components" --query "properties.ConnectionString" -o tsv 2>$null)
    }
    if (-not $cs) { throw "No se pudo obtener el connection string de Application Insights." }

    foreach ($t in Targets) {
        Step "Secrets en $t"
        K $t create namespace $Ns --dry-run=client -o yaml | K $t apply -f - | Out-Null
        K $t -n $Ns create secret generic mesh-token --from-literal=token=$token --dry-run=client -o yaml | K $t apply -f - | Out-Null
        K $t -n $Ns create secret generic appinsights-secret --from-literal=connection-string=$cs --dry-run=client -o yaml | K $t apply -f - | Out-Null
        Ok "mesh-token y appinsights-secret aplicados."
    }
}

function Render([string]$T) {
    $yaml = (& kubectl kustomize "$Root\deploy\$T") -join "`n"
    if ($LASTEXITCODE -ne 0) { throw "kustomize falló para $T" }
    $yaml = $yaml.Replace("__TAG__", (Get-Tag))
    if ($T -eq "cloud") { $yaml = $yaml.Replace("__ACR__", (Get-Acr)) }
    return $yaml
}

function Invoke-Deploy {
    foreach ($t in Targets) {
        Step "Deploy $t (tag $(Get-Tag))"
        if ($t -eq "cloud") {
            K $t apply -f "$Root\deploy\cloud-edge" | Out-Host
        }
        # El Job de topics es inmutable: se recrea en cada deploy.
        K $t -n $Ns delete job redpanda-topics --ignore-not-found 2>$null | Out-Null
        Render $t | K $t apply -f - | Out-Host
        if ($LASTEXITCODE -ne 0) { throw "kubectl apply falló en $t" }
        K $t -n $Ns rollout status statefulset/redpanda --timeout=180s | Out-Host
        K $t -n $Ns wait --for=condition=complete job/redpanda-topics --timeout=180s | Out-Host
        if ($LASTEXITCODE -ne 0) {
            K $t -n $Ns logs job/redpanda-topics --tail=20 | Out-Host
            throw "No se pudieron crear los topics en $t"
        }
        # Reiniciar consumidores para tomar config/imagen nuevas (configmap sin hash)
        K $t -n $Ns rollout restart deploy -l app.kubernetes.io/part-of=voltera-eda | Out-Null
        foreach ($d in (K $t -n $Ns get deploy -l app.kubernetes.io/part-of=voltera-eda -o name)) {
            if ($d -match "declaration-gateway") { continue } # KEDA (0..N)
            K $t -n $Ns rollout status $d --timeout=120s | Out-Host
        }
        Ok "Región $t desplegada."
    }
    Invoke-KnativeDeploy
}

function Invoke-Inject {
    foreach ($t in Targets) {
        $stamp = Get-Date -Format "HHmmss"
        $job = "injector-$stamp"
        Step "Inyectando $N declaraciones en $t ($Rate msg/s) -> job/$job"
        $yaml = (Get-Content "$Root\deploy\jobs\injector.yaml" -Raw).
            Replace("__STAMP__", $stamp).Replace("__N__", "$N").Replace("__RATE__", "$Rate").Replace("__FOREIGN__", [string]::Format([cultureinfo]::InvariantCulture, "{0}", $Foreign)).Replace("__IMAGE__", (Get-Image $t))
        $yaml | K $t apply -f - | Out-Null
        K $t -n $Ns wait --for=condition=complete "job/$job" --timeout=300s | Out-Null
        K $t -n $Ns logs "job/$job" | Where-Object { $_ -notmatch '^\{' } | Out-Host
        (K $t -n $Ns logs "job/$job" | Select-String -Pattern '^\s+([0-9a-f]{32})$' | Select-Object -First 1).Matches.Groups[1].Value |
            Where-Object { $_ } | Set-Content (Join-Path $StateDir "last-trace")
    }
}

function Invoke-Dashboard {
    $port = $DashPort
    if (Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue) {
        throw "El puerto local $port está ocupado. Usa otro: .\scripts\lab.ps1 dashboard -DashPort 18091"
    }
    K onprem -n $Ns rollout status deploy/injector-dashboard --timeout=120s | Out-Host
    Step "Dashboard del injector -> http://localhost:$port (Ctrl+C para cerrar)"
    $pf = Start-Process kubectl -PassThru -NoNewWindow -ArgumentList @("--context", $Ctx["onprem"], "-n", $Ns, "port-forward", "--address", "127.0.0.1", "svc/injector-dashboard", "${port}:80")
    try {
        $up = $false
        for ($i = 0; $i -lt 30 -and -not $pf.HasExited; $i++) {
            if (Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue) { $up = $true; break }
            Start-Sleep -Milliseconds 500
        }
        if (-not $up) { throw "El port-forward no levantó (kubectl terminó o no escucha en $port). Revisa: kubectl --context $($Ctx['onprem']) -n $Ns get pods -l app=injector-dashboard" }
        Ok "Listo. Abre http://127.0.0.1:$port"
        Start-Process "http://127.0.0.1:$port"
        $pf.WaitForExit()
    } finally {
        if (-not $pf.HasExited) { Stop-Process -Id $pf.Id -Force }
    }
}

function Show-Status {
    foreach ($t in Targets) {
        Step "Región $t ($($Ctx[$t]))"
        K $t -n $Ns get pods -o wide | Out-Host
        K $t -n $Ns get scaledobject 2>$null | Out-Host
        Info "Consumer groups (Redpanda):"
        K $t -n $Ns exec redpanda-0 -c redpanda '--' rpk group list 2>$null | Out-Host
        K $t -n $Ns exec redpanda-0 -c redpanda '--' rpk group describe declaration-gateway matching-engine 2>$null |
            Select-String -Pattern "GROUP|TOTAL-LAG|STATE" | Out-Host
        if ($t -eq "cloud") {
            $mesh = Get-SvcJson cloud "mesh-router" "/status"
            if ($mesh) {
                Info "Event Mesh: state=$($mesh.state) heartbeat_age=$($mesh.heartbeat_age) buffered=$($mesh.buffered)"
                $mesh.counters | Format-List | Out-Host
            }
            $fac = Get-SvcJson cloud "facturacion" "/stats"
            if ($fac) { Info "Facturación:"; $fac | ConvertTo-Json -Depth 4 | Out-Host }
            K $t -n $Ns get broker, trigger, kafkasink 2>$null | Out-Host
        } else {
            K $t -n $Ns get kafkasource 2>$null | Out-Host
            K $t -n $Ns exec redpanda-0 -c redpanda '--' rpk group describe knative-declaration-forward 2>$null |
                Select-String -Pattern "GROUP|TOTAL-LAG|STATE" | Out-Host
        }
    }
}

function Wait-MeshState([string]$State, [int]$TimeoutSec) {
    $deadline = (Get-Date).AddSeconds($TimeoutSec)
    while ((Get-Date) -lt $deadline) {
        $m = Get-SvcJson cloud "mesh-router" "/status"
        if ($m -and $m.state -eq $State) { return $m }
        Start-Sleep 2
    }
    return $null
}

function Invoke-FailoverTest {
    $script:Target = "onprem"
    Step "Precondición: el mesh ve el heartbeat de onprem (NORMAL)"
    K onprem -n $Ns scale deploy/matching-engine --replicas=1 | Out-Null
    if (-not (Wait-MeshState "NORMAL" 90)) { throw "El mesh no está en NORMAL. Revisa mesh-bridge (onprem) y mesh-router (cloud)." }
    $before = Get-SvcJson cloud "facturacion" "/stats"
    $prevFailover = [int]($before.by_route.'onprem->cloud')
    Ok "NORMAL. Facturas onprem->cloud previas: $prevFailover"

    Step "Apagando matching-engine LOCAL (scale 0)"
    $t0 = Get-Date
    K onprem -n $Ns scale deploy/matching-engine --replicas=0 | Out-Null
    K onprem -n $Ns wait --for=delete pod -l app=matching-engine --timeout=60s 2>$null | Out-Null

    Invoke-Inject

    Step "Esperando FAILOVER_ACTIVE en el mesh"
    $m = Wait-MeshState "FAILOVER_ACTIVE" 90
    if (-not $m) { throw "El mesh no entró en failover." }
    $detect = [int]((Get-Date) - $t0).TotalSeconds
    Ok "FAILOVER_ACTIVE (~${detect}s tras apagar). Motivo: $(($m.transitions | Select-Object -Last 1).reason)"

    Step "Esperando calces procesados por la nube para posiciones onprem"
    $after = $null
    for ($i = 0; $i -lt 30; $i++) {
        Start-Sleep 3
        $after = Get-SvcJson cloud "facturacion" "/stats"
        if ([int]($after.by_route.'onprem->cloud') -gt $prevFailover) { break }
    }
    $newFailover = [int]($after.by_route.'onprem->cloud') - $prevFailover

    Step "Restaurando matching-engine LOCAL"
    K onprem -n $Ns scale deploy/matching-engine --replicas=1 | Out-Null
    $back = Wait-MeshState "NORMAL" 120

    Step "Resultado"
    $mesh = Get-SvcJson cloud "mesh-router" "/status"
    Info "Transiciones del mesh:"
    $mesh.transitions | Format-Table at, from, to, reason -AutoSize | Out-Host
    Info "Facturación by_route: $($after.by_route | ConvertTo-Json -Compress)"
    if ($newFailover -gt 0 -and $back) {
        Ok "PASS: $newFailover calces onprem->cloud durante el failover; mesh volvió a NORMAL."
    } else {
        Warn "FAIL: calces onprem->cloud nuevos=$newFailover, vuelta a NORMAL=$([bool]$back)"
        exit 1
    }
}


function Show-KnativeKql {
    Step "Consultas para Application Insights > Logs (canal Knative)"
    Write-Host @'
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

// 3) ¿Se cortó el traceparent? Más de un operation_Id por declaración = traza partida
union dependencies, requests
| where timestamp > ago(2h) and tostring(customDimensions["messaging.message.id"]) != ""
| summarize trazas = dcount(operation_Id), roles = make_set(cloud_RoleName) by msg = tostring(customDimensions["messaging.message.id"])
| where array_length(roles) > 3
| summarize unidas = countif(trazas == 1), partidas = countif(trazas > 1)
'@
}

function Get-TopicHw([string]$T, [string]$Topic) {
    $hdr = $null; $sum = [long]0
    foreach ($l in (K $T -n $Ns exec redpanda-0 -c redpanda '--' rpk topic describe $Topic -p 2>$null)) {
        $f = $l.Trim() -split '\s+'
        if ($f -contains 'HIGH-WATERMARK') { $hdr = [array]::IndexOf($f, 'HIGH-WATERMARK'); continue }
        if ($null -ne $hdr -and $f[0] -match '^\d+$') { $sum += [long]$f[$hdr] }
    }
    return $sum
}

function Invoke-KnativeDeploy {
    foreach ($t in Targets) {
        $crd = if ($t -eq "cloud") { "kafkasinks.eventing.knative.dev" } else { "kafkasources.sources.knative.dev" }
        K $t get crd $crd 2>$null | Out-Null
        if ($LASTEXITCODE -ne 0) { Warn "Knative no instalado en $t (addons -Knative); se omite deploy\knative"; continue }
        Step "Knative en $t"
        $tok = (Get-Content (Join-Path $StateDir "mesh-token") -Raw).Trim()
        if ($t -eq "cloud") {
            ((& kubectl kustomize "$Root\deploy\knative\cloud-edge") -join "`n").Replace("__MESH_TOKEN__", $tok) | K $t apply -f - | Out-Host
        }
        ((& kubectl kustomize "$Root\deploy\knative\$t") -join "`n").Replace("__MESH_TOKEN__", $tok) | K $t apply -f - | Out-Host
    }
}

function Invoke-KnativeTest {
    $script:Target = "onprem"
    if ($Foreign -le 0) { $script:Foreign = 0.2 }
    Step "Línea base (high-watermark de los topics)"
    $f0 = Get-TopicHw onprem "voltera.declaration.forward.v1"
    $i0 = Get-TopicHw cloud "voltera.ingress.declarations"
    $d0 = Get-TopicHw cloud "voltera.declaration.dlq.v1"
    Info "forward(onprem)=$f0 ingress(nube)=$i0 dlq(nube)=$d0"
    Invoke-Inject
    Step "Esperando que Knative entregue en la nube (hasta 90 s)"
    $df = 0; $di = 0
    for ($i = 0; $i -lt 30; $i++) {
        Start-Sleep 3
        $df = (Get-TopicHw onprem "voltera.declaration.forward.v1") - $f0
        $di = (Get-TopicHw cloud "voltera.ingress.declarations") - $i0
        if ($df -gt 0 -and $di -ge $df) { break }
    }
    $dd = (Get-TopicHw cloud "voltera.declaration.dlq.v1") - $d0
    Info "reenviadas por el gateway onprem: $df | llegadas a la ingesta de la nube: $di | DLQ: $dd"
    if ($df -gt 0 -and $di -eq $df -and $dd -eq 0) { Ok "PASS: $df declaraciones ajenas cruzaron onprem -> nube vía Knative, sin pérdida ni DLQ" }
    else { Warn "FAIL (si la nube también recibe inyección propia, di puede exceder df)"; $script:ExitCode = 1 }
    Show-KnativeKql
}

function Show-Trace {
    $id = $TraceId
    if (-not $id) {
        $f = Join-Path $StateDir "last-trace"
        if (Test-Path $f) { $id = (Get-Content $f -Raw).Trim() }
    }
    if (-not $id) { throw "Indica -TraceId (lo imprime el injector)." }
    $kql = @"
union requests, dependencies
| where timestamp > ago(2h)
| where operation_Id == '$id'
| project timestamp, cloud_RoleName, region = tostring(customDimensions['cloud.region']), name, itemType, duration, success
| order by timestamp asc
"@
    Step "Traza $id"
    Write-Host $kql
    Info "Portal: Application Insights 'voltera-dev-appinsights' -> Transaction search -> pega el trace id (operation_Id),"
    Info "o Logs -> ejecuta la consulta anterior. La vista end-to-end muestra ambos clusters."
    $hasExt = (& az extension list --query "[?name=='application-insights'] | length(@)" -o tsv 2>$null)
    if ($hasExt -eq "1") {
        Step "Ejecutando consulta (puede tardar ~2-3 min en ingerirse)"
        & az monitor app-insights query --app "voltera-dev-appinsights" -g $ResourceGroup --analytics-query $kql -o table
    } else {
        Info "Tip: 'az extension add -n application-insights' para ejecutarla desde aquí."
    }
}

switch ($Command) {
    "cleanup" { Invoke-Cleanup }
    "addons" { Invoke-Addons }
    "build" { Invoke-Build }
    "secrets" { Invoke-Secrets }
    "deploy" { Invoke-Deploy }
    "up" { Invoke-Build; Invoke-Secrets; Invoke-Deploy }
    "inject" { Invoke-Inject }
    "dashboard" { Invoke-Dashboard }
    "status" { Show-Status }
    "failover-test" { Invoke-FailoverTest }
    "knative-test" { Invoke-KnativeTest; if ($script:ExitCode) { exit $script:ExitCode } }
    "knative-kql" { Show-KnativeKql }
    "trace" { Show-Trace }
    "logs" { $t = if ($Target -eq "all") { "onprem" } else { $Target }; K $t -n $Ns logs -f "deploy/$Service" --tail=50 }
    "aks" { & "$Root\infra\azure-terraform\scripts\aks-power.ps1" -Action $Action -ResourceGroup $ResourceGroup -ClusterName $AksName }
    "down" {
        foreach ($t in Targets) { Confirm-Action "¿Borrar ns $Ns en $t?"; K $t delete ns $Ns --ignore-not-found | Out-Host }
    }
    default { Get-Help $PSCommandPath -Detailed }
}
