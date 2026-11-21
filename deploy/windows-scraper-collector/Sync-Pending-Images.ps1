param(
    [Parameter(Mandatory = $true)]
    [ValidateSet('araujo', 'pacheco')]
    [string]$Source
)

$ErrorActionPreference = 'Stop'
$StateRoot = Join-Path $env:LOCALAPPDATA 'PrecoBaoCollector'
$settingsPath = Join-Path $StateRoot 'settings.json'
if (-not (Test-Path $settingsPath)) { throw 'Execute Install-Collector.ps1 primeiro.' }
$settings = Get-Content -Raw $settingsPath | ConvertFrom-Json
if (-not $settings.api_ready) { throw 'A API ainda não está marcada como pronta.' }
$secretPath = Join-Path $StateRoot 'hmac-secret.dpapi'
if (-not (Test-Path $secretPath)) { throw 'Configure o segredo com Set-CollectorSecret.ps1.' }
$secure = Get-Content -Raw $secretPath | ConvertTo-SecureString
$ptr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
try { $env:SCRAPER_INGEST_HMAC_SECRET = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($ptr) }
finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($ptr) }

$stateDir = [IO.Path]::GetFullPath([string]$settings.state_dir)
$lockDirectory = Join-Path $stateDir 'locks'
New-Item -ItemType Directory -Force -Path $lockDirectory | Out-Null
$lockPath = Join-Path $lockDirectory 'collector.running.lock'
try { $lock = [IO.File]::Open($lockPath, 'OpenOrCreate', 'ReadWrite', 'None') }
catch {
    $env:SCRAPER_INGEST_HMAC_SECRET = $null
    throw 'O coletor já está em execução; a outbox não foi alterada.'
}

try {
    $env:SCRAPER_INGEST_URL = [string]$settings.api_url
    $env:SCRAPER_COLLECTOR_STATE_DIR = $stateDir
    $python = Join-Path $settings.repository_root 'backend\.venv\Scripts\python.exe'
    if (-not (Test-Path $python)) { throw 'Ambiente Python não encontrado. Reexecute Install-Collector.ps1.' }
    Push-Location (Join-Path $settings.repository_root 'backend')
    try {
        & $python -m app.scrapers.scraper_collector $Source --images-only
        if ($LASTEXITCODE -ne 0) { throw "A API não confirmou a outbox de imagens (código $LASTEXITCODE)." }
    } finally { Pop-Location }
} finally {
    $env:SCRAPER_INGEST_HMAC_SECRET = $null
    $env:SCRAPER_INGEST_URL = $null
    $env:SCRAPER_COLLECTOR_STATE_DIR = $null
    $lock.Dispose()
}
