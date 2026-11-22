param(
    [ValidateSet('Inventory', 'DryRun', 'Execute')]
    [string]$Mode = 'DryRun',
    [ValidateSet('araujo', 'pacheco')]
    [string]$Source,
    [ValidateRange(1, 10)]
    [int]$Limit = 3,
    [switch]$BackupVerified,
    [switch]$IncludeGenericFallback
)

$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'Collector-IO.ps1')
$StateRoot = Join-Path $env:LOCALAPPDATA 'PrecoBaoCollector'
$settingsPath = Join-Path $StateRoot 'settings.json'
if (-not (Test-Path -LiteralPath $settingsPath)) { throw 'Execute Install-Collector.ps1 primeiro.' }
$settings = Read-CollectorJson -Path $settingsPath
if (-not $settings.api_ready) { throw 'API ainda não está habilitada na configuração local.' }
if ($Mode -eq 'Execute' -and -not $BackupVerified) {
    throw 'A execução com escrita exige -BackupVerified após validar o backup atual.'
}
if (($Mode -eq 'DryRun' -or $Mode -eq 'Execute') -and -not $Source) {
    throw 'Escolha uma origem para a execução manual: -Source araujo ou -Source pacheco.'
}
if ($Source -and ($settings.sources.$Source.mode -ne 'DIRECT' -or -not $settings.sources.$Source.access_confirmed)) {
    throw "A origem $Source não está em DIRECT com acesso confirmado na configuração local."
}
if ([TimeZoneInfo]::Local.Id -notin @('E. South America Standard Time', 'SA Eastern Standard Time')) {
    throw 'Configure o fuso do Windows para America/Sao_Paulo (UTC-03:00).'
}

$apiUri = [Uri]([string]$settings.api_url)
if ($apiUri.Scheme -ne 'https' -or $apiUri.Host -ne 'precobao.app' -or $apiUri.UserInfo) {
    throw 'A configuração deve apontar para a API HTTPS de produção esperada.'
}
$healthUri = [UriBuilder]::new($apiUri)
$healthUri.Path = '/backend/health/ready'
$healthUri.Query = ''
$health = Invoke-RestMethod -Method Get -Uri $healthUri.Uri -TimeoutSec 10
if ($health.status -ne 'ready') { throw 'A API não passou na verificação de prontidão.' }

$secretPath = Join-Path $StateRoot 'hmac-secret.dpapi'
if (-not (Test-Path -LiteralPath $secretPath -PathType Leaf)) { throw 'Segredo HMAC DPAPI não configurado.' }
$encryptedSecret = Read-CollectorUtf8 -Path $secretPath
$secureSecret = ConvertTo-SecureString -String $encryptedSecret -ErrorAction Stop
if ($secureSecret.Length -lt 32) { $secureSecret.Dispose(); throw 'Segredo HMAC inválido.' }
$ptr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secureSecret)
try { $env:SCRAPER_INGEST_HMAC_SECRET = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($ptr) }
finally {
    [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($ptr)
    $secureSecret.Dispose()
}

$stateDir = [IO.Path]::GetFullPath([string]$settings.state_dir)
$python = Join-Path $settings.repository_root 'backend\.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $python)) { throw 'Ambiente Python do coletor não encontrado.' }
$arguments = @('-m', 'scripts.image_backfill')
if ($Mode -eq 'Inventory') { $arguments += '--inventory' }
if ($Mode -eq 'DryRun') { $arguments += @('--source', $Source, '--limit', [string]$Limit) }
if ($Mode -eq 'Execute') { $arguments += @('--execute', '--source', $Source, '--limit', [string]$Limit) }
if ($Mode -eq 'Execute' -and $IncludeGenericFallback) { $arguments += '--include-generic-fallback' }

$previousEnvironment = @{}
$environment = @{
    SCRAPER_INGEST_URL = [string]$settings.api_url
    SCRAPER_COLLECTOR_STATE_DIR = $stateDir
    SCRAPER_EXTERNAL_RATE_LIMIT_ENABLED = 'true'
    SCRAPER_EXTERNAL_RATE_LIMIT_MAX_REQUESTS = '50'
    SCRAPER_EXTERNAL_RATE_LIMIT_WINDOW_SECONDS = '900'
    SCRAPER_EXTERNAL_RATE_LIMIT_PER_SOURCE_MAX_REQUESTS = '25'
    SCRAPER_EXTERNAL_RATE_LIMIT_IMAGE_BACKFILL_PER_SOURCE = '10'
    SCRAPER_EXTERNAL_RATE_LIMIT_STATE_DIR = (Join-Path $stateDir 'rate-limit')
    SCRAPER_EXTERNAL_RATE_LIMIT_WAIT_MODE = 'stop'
    ARAUJO_TLS_VERIFY = 'true'
    PACHECO_TLS_VERIFY = 'true'
    IMAGE_BACKFILL_ONLY = 'true'
    PYTHONUTF8 = '1'
    PYTHONIOENCODING = 'utf-8'
}
foreach ($name in $environment.Keys) {
    $previousEnvironment[$name] = [Environment]::GetEnvironmentVariable($name, 'Process')
    [Environment]::SetEnvironmentVariable($name, [string]$environment[$name], 'Process')
}
try {
    Push-Location (Join-Path $settings.repository_root 'backend')
    try {
        & $python @arguments
        if ($LASTEXITCODE -ne 0) { throw "Recuperação de imagens terminou com código $LASTEXITCODE." }
    } finally { Pop-Location }
} finally {
    foreach ($name in $environment.Keys) {
        [Environment]::SetEnvironmentVariable($name, $previousEnvironment[$name], 'Process')
    }
    $env:SCRAPER_INGEST_HMAC_SECRET = $null
}
