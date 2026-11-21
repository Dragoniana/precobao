param(
    [ValidateSet('araujo', 'pacheco', 'both')]
    [string]$Source = 'both',
    [string]$ScheduledSlot = ''
)

$ErrorActionPreference = 'Stop'
$StateRoot = Join-Path $env:LOCALAPPDATA 'PrecoBaoCollector'
$settingsPath = Join-Path $StateRoot 'settings.json'
if (-not (Test-Path $settingsPath)) { throw 'Execute Install-Collector.ps1 primeiro.' }
$settings = Get-Content -Raw $settingsPath | ConvertFrom-Json
if (-not $settings.api_ready -or -not $settings.collector_enabled) {
    throw 'API/coletor não habilitados na configuração local.'
}
if ([TimeZoneInfo]::Local.Id -notin @('E. South America Standard Time', 'SA Eastern Standard Time')) {
    throw 'Configure o fuso do Windows para America/Sao_Paulo (UTC-03:00) antes de executar.'
}
$secretPath = Join-Path $StateRoot 'hmac-secret.dpapi'
if (-not (Test-Path $secretPath)) { throw 'Configure o segredo com Set-CollectorSecret.ps1.' }
$secure = Get-Content -Raw $secretPath | ConvertTo-SecureString
$ptr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
try { $env:SCRAPER_INGEST_HMAC_SECRET = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($ptr) }
finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($ptr) }

$stateDir = [IO.Path]::GetFullPath([string]$settings.state_dir)
$lockDirectory = Join-Path $stateDir 'locks'
New-Item -ItemType Directory -Force -Path $lockDirectory | Out-Null
New-Item -ItemType Directory -Force -Path (Join-Path $stateDir 'sources\araujo') | Out-Null
New-Item -ItemType Directory -Force -Path (Join-Path $stateDir 'sources\pacheco') | Out-Null
$lockPath = Join-Path $lockDirectory 'collector.running.lock'
try { $lock = [IO.File]::Open($lockPath, 'OpenOrCreate', 'ReadWrite', 'None') }
catch {
    Add-Content $settings.log_file "$(Get-Date -Format o) SKIPPED already_running"
    Write-Host 'Coleta já em execução; esta chamada foi ignorada.'
    exit 0
}
try {
    Remove-Item -LiteralPath $settings.stop_file -Force -ErrorAction SilentlyContinue
    $env:SCRAPER_INGEST_URL = $settings.api_url
    $env:SCRAPER_COLLECTOR_ENABLED = 'true'
    $env:SCRAPER_COLLECTION_ENV_AUTHORIZED = [string]$settings.collection_environment_authorized
    $env:SCRAPER_COLLECTOR_STATE_DIR = $stateDir
    $env:SCRAPER_COLLECTOR_STOP_FILE = $settings.stop_file
    $env:SCRAPER_COLLECTOR_SCHEDULE_TIMES = ($settings.schedule_times -join ',')
    $env:SCRAPER_COLLECTOR_TIMEZONE = $settings.timezone
    foreach ($name in @('araujo', 'pacheco')) {
        $entry = $settings.sources.$name
        $prefix = $name.ToUpperInvariant()
        [Environment]::SetEnvironmentVariable("${prefix}_SOURCE_MODE", $entry.mode, 'Process')
        [Environment]::SetEnvironmentVariable(
            "${prefix}_STATE_DIR",
            (Join-Path (Join-Path $stateDir 'sources') $name),
            'Process'
        )
        if ($name -eq 'araujo') {
            $env:ARAUJO_PUBLIC_CATALOG_AUTHORIZED = 'true'
            $env:ARAUJO_ACCESS_CONFIRMED = [string]$entry.access_confirmed
        } else {
            $env:PACHECO_PUBLIC_API_AUTHORIZED = 'true'
            $env:PACHECO_ACCESS_CONFIRMED = [string]$entry.access_confirmed
        }
    }
    $sources = if ($Source -eq 'both') { @('araujo', 'pacheco') } else { @($Source) }
    $python = Join-Path $settings.repository_root 'backend\.venv\Scripts\python.exe'
    if (-not (Test-Path $python)) { throw 'Ambiente Python não encontrado. Reexecute Install-Collector.ps1.' }
    $slotKey = ''
    if ($ScheduledSlot) {
        $slotParts = $ScheduledSlot.Split(':')
        $slotTime = [DateTime]::Today.AddHours([int]$slotParts[0]).AddMinutes([int]$slotParts[1])
        if ($slotTime -gt (Get-Date)) { $slotTime = $slotTime.AddDays(-1) }
        $slotKey = $slotTime.ToString('yyyy-MM-ddTHH:mm')
        $slotStatePath = Join-Path $StateRoot 'last-scheduled-slot.txt'
        if ((Test-Path $slotStatePath) -and (Get-Content -Raw $slotStatePath).Trim() -eq $slotKey) {
            Add-Content $settings.log_file "$(Get-Date -Format o) SKIPPED duplicate_slot=$slotKey"
            exit 0
        }
        $delayMinutes = [math]::Floor(((Get-Date) - $slotTime).TotalMinutes)
        if ($delayMinutes -gt [int]$settings.missed_run_grace_minutes) {
            Set-Content $slotStatePath $slotKey -Encoding ascii
            Add-Content $settings.log_file "$(Get-Date -Format o) MISSED slot=$slotKey delay_minutes=$delayMinutes recovery=skipped"
            Write-Host "Horário perdido há $delayMinutes min; recuperação fora da janela permitida."
            exit 0
        }
        if ($delayMinutes -gt 5) {
            Add-Content $settings.log_file "$(Get-Date -Format o) MISSED slot=$slotKey delay_minutes=$delayMinutes recovery=single_run"
        }
        Set-Content $slotStatePath $slotKey -Encoding ascii
    }
    foreach ($item in $sources) {
        if ($settings.sources.$item.mode -ne 'DIRECT' -or -not $settings.sources.$item.access_confirmed) {
            Add-Content $settings.log_file "$(Get-Date -Format o) SKIPPED source=$item mode_disabled_or_unconfirmed"
            Write-Host "$item ignorada: modo DIRECT e confirmação de acesso não configurados."
            continue
        }
        Add-Content $settings.log_file "$(Get-Date -Format o) START source=$item slot=$ScheduledSlot"
        Push-Location (Join-Path $settings.repository_root 'backend')
        try { & $python -m app.scrapers.scraper_collector $item *>> $settings.log_file }
        finally { Pop-Location }
        Add-Content $settings.log_file "$(Get-Date -Format o) FINISH source=$item exit=$LASTEXITCODE"
        if (Test-Path $settings.stop_file) { break }
    }
} finally {
    $env:SCRAPER_INGEST_HMAC_SECRET = $null
    $env:ARAUJO_STATE_DIR = $null
    $env:PACHECO_STATE_DIR = $null
    $env:SCRAPER_COLLECTOR_STATE_DIR = $null
    $lock.Dispose()
}
