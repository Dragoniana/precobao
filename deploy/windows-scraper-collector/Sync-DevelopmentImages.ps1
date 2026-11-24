param(
    [switch]$Execute,
    [switch]$BackupVerified,
    [switch]$AllEligible,
    [int]$Limit = 0
)

$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'Collector-IO.ps1')
$StateRoot = Join-Path $env:LOCALAPPDATA 'PrecoBaoCollector'
$settingsPath = Join-Path $StateRoot 'settings.json'
if (-not (Test-Path -LiteralPath $settingsPath)) {
    throw 'Execute Install-Collector.ps1 primeiro.'
}
$settings = Read-CollectorJson -Path $settingsPath
if (-not $settings.api_ready) { throw 'A API ainda não está marcada como pronta.' }
if ([string]::IsNullOrWhiteSpace($env:PRECOBAO_DEVELOPMENT_DATABASE_URL)) {
    throw 'Configure PRECOBAO_DEVELOPMENT_DATABASE_URL no processo sem exibir seu valor.'
}
if ($Execute -and -not $BackupVerified) {
    throw 'A execução exige -BackupVerified após a confirmação do backup recuperável da produção.'
}
if ($Execute -and $AllEligible -and $Limit -ne 0) {
    throw 'Use -AllEligible ou -Limit, não ambos.'
}
if ($Execute -and -not $AllEligible -and ($Limit -lt 1 -or $Limit -gt 50)) {
    throw 'A execução exige -Limit entre 1 e 50 para limitar cada lote.'
}
if (-not $Execute -and ($Limit -ne 0 -or $AllEligible)) {
    throw '-Limit e -AllEligible só podem ser usados junto com -Execute.'
}

$secretPath = Join-Path $StateRoot 'hmac-secret.dpapi'
if (-not (Test-Path -LiteralPath $secretPath)) {
    throw 'Configure o segredo com Set-CollectorSecret.ps1.'
}
$secure = [IO.File]::ReadAllText($secretPath, [Text.Encoding]::ASCII) | ConvertTo-SecureString
$ptr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
$previous = @{}
foreach ($name in @(
    'SCRAPER_INGEST_HMAC_SECRET',
    'SCRAPER_INGEST_URL',
    'SCRAPER_COLLECTOR_STATE_DIR',
    'PRECOBAO_PRODUCTION_BACKUP_VERIFIED'
)) {
    $previous[$name] = [Environment]::GetEnvironmentVariable($name, 'Process')
}

$lockPath = Join-Path (Join-Path $settings.state_dir 'locks') 'development-image-migration.lock'
$lock = $null
try {
    $env:SCRAPER_INGEST_HMAC_SECRET = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($ptr)
    $env:SCRAPER_INGEST_URL = [string]$settings.api_url
    $env:SCRAPER_COLLECTOR_STATE_DIR = [IO.Path]::GetFullPath([string]$settings.state_dir)
    if ($Execute) { $env:PRECOBAO_PRODUCTION_BACKUP_VERIFIED = '1' }
    $lockDirectory = Split-Path -Parent $lockPath
    New-Item -ItemType Directory -Force -Path $lockDirectory | Out-Null
    try { $lock = [IO.File]::Open($lockPath, 'OpenOrCreate', 'ReadWrite', 'None') }
    catch { throw 'Outra migração de imagens já está em execução; nenhum arquivo foi alterado.' }

    $python = Join-Path $settings.repository_root 'backend\.venv\Scripts\python.exe'
    if (-not (Test-Path -LiteralPath $python)) {
        throw 'Ambiente Python do coletor não encontrado.'
    }
    Push-Location (Join-Path $settings.repository_root 'backend')
    try {
        if ($Execute) {
            if ($AllEligible) {
                & $python -m 'scripts.sync_development_images_to_production' --execute --all-eligible
            } else {
                & $python -m 'scripts.sync_development_images_to_production' --execute --limit $Limit
            }
        } else {
            & $python -m 'scripts.sync_development_images_to_production' --dry-run
        }
        if ($LASTEXITCODE -ne 0) {
            throw "Migração interrompida com código $LASTEXITCODE; consulte apenas o relatório local sanitizado."
        }
    } finally { Pop-Location }
} finally {
    if ($null -ne $lock) { $lock.Dispose() }
    [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($ptr)
    $secure.Dispose()
    foreach ($name in $previous.Keys) {
        [Environment]::SetEnvironmentVariable($name, $previous[$name], 'Process')
    }
}
