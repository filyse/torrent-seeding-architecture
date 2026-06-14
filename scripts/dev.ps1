param(
    [ValidateSet("up", "down", "logs", "test", "sync", "status")]
    [string]$Command = "up",
    [string]$ApiBase = "http://127.0.0.1:8000",
    [string]$ApiKey = ""
)

$ErrorActionPreference = "Stop"

function Invoke-Health {
    param([string]$Url)
    try {
        $r = Invoke-RestMethod -Uri $Url -Method Get -TimeoutSec 10
        Write-Host "OK  $Url"
        return $r
    }
    catch {
        Write-Host "ERR $Url"
        return $null
    }
}

switch ($Command) {
    "up" {
        docker compose up -d --build
        Write-Host ""
        Write-Host "Health checks..."
        Invoke-Health "$ApiBase/api/v1/health" | Out-Null
        Invoke-Health "http://127.0.0.1:8081/health" | Out-Null
        Write-Host ""
        Write-Host "Web UI: http://127.0.0.1:3000"
    }
    "down" {
        docker compose down
    }
    "logs" {
        docker compose logs -f
    }
    "test" {
        python -m pytest -q
    }
    "sync" {
        $headers = @{}
        if ($ApiKey -ne "") {
            $headers["X-API-Key"] = $ApiKey
        }
        $resp = Invoke-RestMethod -Uri "$ApiBase/api/v1/jobs/sync-runtime" -Method Post -Headers $headers
        $resp | ConvertTo-Json -Depth 5
    }
    "status" {
        Invoke-Health "$ApiBase/api/v1/health" | ConvertTo-Json -Depth 5
        Invoke-Health "http://127.0.0.1:8081/health" | ConvertTo-Json -Depth 5
    }
}
