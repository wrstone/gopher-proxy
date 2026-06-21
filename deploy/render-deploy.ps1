# Deploy wrstone/gopher-proxy to Render via API.
# Usage:
#   $env:RENDER_API_KEY = "rnd_..."
#   .\deploy\render-deploy.ps1

$ErrorActionPreference = "Stop"

if (-not $env:RENDER_API_KEY) {
    Write-Error "Set RENDER_API_KEY first. Create one at https://dashboard.render.com/u/settings#api-keys"
}

$headers = @{
    Authorization = "Bearer $env:RENDER_API_KEY"
    Accept        = "application/json"
    "Content-Type" = "application/json"
}

$owners = Invoke-RestMethod -Uri "https://api.render.com/v1/owners?limit=20" -Headers $headers
$ownerId = $owners[0].owner.id
if (-not $ownerId) { throw "No Render workspace found for this API key." }

Write-Host "Using workspace: $($owners[0].owner.name) ($ownerId)"

$existing = Invoke-RestMethod -Uri "https://api.render.com/v1/services?name=wrstone-gopher-proxy&limit=20" -Headers $headers
$serviceId = $null
foreach ($entry in $existing) {
    if ($entry.service.name -eq "wrstone-gopher-proxy") {
        $serviceId = $entry.service.id
        break
    }
}

if (-not $serviceId) {
    $body = @{
        type    = "web_service"
        name    = "wrstone-gopher-proxy"
        ownerId = $ownerId
        repo    = "https://github.com/wrstone/gopher-proxy"
        branch  = "main"
        autoDeploy = "yes"
        serviceDetails = @{
            runtime = "python"
            plan    = "free"
            region  = "oregon"
            healthCheckPath = "/"
            envSpecificDetails = @{
                buildCommand = "true"
                startCommand = "python server.py"
            }
        }
        envVars = @(
            @{ key = "BIND_HOST"; value = "0.0.0.0" },
            @{ key = "GOPHER_START"; value = "gopher://sdf.org/users/wrstone/" }
        )
    } | ConvertTo-Json -Depth 6

    $created = Invoke-RestMethod -Method POST -Uri "https://api.render.com/v1/services" -Headers $headers -Body $body
    $serviceId = $created.service.id
    Write-Host "Created service: $serviceId"
} else {
    Write-Host "Service already exists: $serviceId"
}

$deploy = Invoke-RestMethod -Method POST -Uri "https://api.render.com/v1/services/$serviceId/deploys" -Headers $headers -Body "{}" 
Write-Host "Deploy triggered: $($deploy.id)"
Write-Host "URL: https://wrstone-gopher-proxy.onrender.com/"