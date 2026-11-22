param(
    [ValidateSet('araujo', 'pacheco', 'both')]
    [string]$Source = 'both',
    [string]$ScheduledSlot = '',
    [ValidateRange(0, 59)]
    [int]$ScheduledOffsetMinutes = 0,
    [ValidateRange(0, 60)]
    [int]$ScheduledIntervalMinutes = 0,
    [ValidateRange(0, 50)]
    [int]$MaxProductsPerRun = 0
)

$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'Collector-IO.ps1')
$StateRoot = Join-Path $env:LOCALAPPDATA 'PrecoBaoCollector'
$settingsPath = Join-Path $StateRoot 'settings.json'
if (-not (Test-Path $settingsPath)) { throw 'Execute Install-Collector.ps1 primeiro.' }
$settings = Read-CollectorJson -Path $settingsPath
if (-not $settings.api_ready -or -not $settings.collector_enabled) {
    throw 'API/coletor não habilitados na configuração local.'
}
if (($ScheduledSlot -or $ScheduledIntervalMinutes -gt 0) -and $Source -eq 'both') {
    throw 'Uma tarefa agendada deve executar uma única farmácia.'
}
if ($ScheduledIntervalMinutes -gt 0 -and $ScheduledOffsetMinutes -ge $ScheduledIntervalMinutes) {
    throw 'O deslocamento do agendamento deve ser menor que o intervalo.'
}
if ($MaxProductsPerRun -gt 0) {
    $env:SCRAPER_COLLECTOR_MAX_PRODUCTS_PER_RUN = [string]$MaxProductsPerRun
} else {
    Remove-Item Env:SCRAPER_COLLECTOR_MAX_PRODUCTS_PER_RUN -ErrorAction SilentlyContinue
}
if ($ScheduledSlot -or $ScheduledIntervalMinutes -gt 0) {
    $env:SCRAPER_COLLECTOR_SCHEDULED = 'true'
} else {
    Remove-Item Env:SCRAPER_COLLECTOR_SCHEDULED -ErrorAction SilentlyContinue
}
if ([TimeZoneInfo]::Local.Id -notin @('E. South America Standard Time', 'SA Eastern Standard Time')) {
    throw 'Configure o fuso do Windows para America/Sao_Paulo (UTC-03:00) antes de executar.'
}
$secretPath = Join-Path $StateRoot 'hmac-secret.dpapi'
if (-not (Test-Path $secretPath)) { throw 'Configure o segredo com Set-CollectorSecret.ps1.' }
$secure = [IO.File]::ReadAllText($secretPath, [Text.Encoding]::ASCII) | ConvertTo-SecureString
$ptr = [Runtime.InteropServices.Marshal]::SecureStringToBSTR($secure)
try { $env:SCRAPER_INGEST_HMAC_SECRET = [Runtime.InteropServices.Marshal]::PtrToStringBSTR($ptr) }
finally { [Runtime.InteropServices.Marshal]::ZeroFreeBSTR($ptr) }

$stateDir = [IO.Path]::GetFullPath([string]$settings.state_dir)
$lockDirectory = Join-Path $stateDir 'locks'
New-Item -ItemType Directory -Force -Path $lockDirectory | Out-Null
New-Item -ItemType Directory -Force -Path (Join-Path $stateDir 'sources\araujo') | Out-Null
New-Item -ItemType Directory -Force -Path (Join-Path $stateDir 'sources\pacheco') | Out-Null
$previousPythonUtf8 = $env:PYTHONUTF8
$previousPythonIoEncoding = $env:PYTHONIOENCODING
try {
    Remove-Item -LiteralPath $settings.stop_file -Force -ErrorAction SilentlyContinue
    $env:SCRAPER_INGEST_URL = $settings.api_url
    $env:SCRAPER_COLLECTOR_ENABLED = 'true'
    $env:SCRAPER_COLLECTION_ENV_AUTHORIZED = [string]$settings.collection_environment_authorized
    $env:SCRAPER_COLLECTOR_STATE_DIR = $stateDir
    $env:SCRAPER_EXTERNAL_RATE_LIMIT_ENABLED = 'true'
    $env:SCRAPER_EXTERNAL_RATE_LIMIT_MAX_REQUESTS = '50'
    $env:SCRAPER_EXTERNAL_RATE_LIMIT_WINDOW_SECONDS = '900'
    $env:SCRAPER_EXTERNAL_RATE_LIMIT_STATE_DIR = Join-Path $stateDir 'rate-limit'
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
    $configuredSources = @($sources | Where-Object {
        $entry = $settings.sources.PSObject.Properties[$_].Value
        $entry.mode -eq 'DIRECT' -and $entry.access_confirmed
    })
    if ($configuredSources.Count -eq 0) {
        Write-CollectorLog -Path $settings.log_file -Event 'FAILED source_disabled_or_unconfirmed'
        exit 8
    }
    $python = Join-Path $settings.repository_root 'backend\.venv\Scripts\python.exe'
    if (-not (Test-Path $python)) { throw 'Ambiente Python não encontrado. Reexecute Install-Collector.ps1.' }
    $slotKey = ''
    $slotLabel = 'manual'
    if ($ScheduledSlot -or $ScheduledIntervalMinutes -gt 0) {
        $currentTime = Get-Date
        if ($ScheduledIntervalMinutes -gt 0) {
            $baseTime = $currentTime.Date.AddMinutes($ScheduledOffsetMinutes)
            if ($baseTime -gt $currentTime) { $baseTime = $baseTime.AddDays(-1) }
            $elapsedMinutes = ($currentTime - $baseTime).TotalMinutes
            $slotIndex = [math]::Floor($elapsedMinutes / $ScheduledIntervalMinutes)
            $slotTime = $baseTime.AddMinutes($slotIndex * $ScheduledIntervalMinutes)
            $slotLabel = "every-$($ScheduledIntervalMinutes)m-offset-$($ScheduledOffsetMinutes)m"
        } else {
            $slotParts = $ScheduledSlot.Split(':')
            $slotTime = [DateTime]::Today.AddHours([int]$slotParts[0]).AddMinutes([int]$slotParts[1])
            if ($slotTime -gt $currentTime) { $slotTime = $slotTime.AddDays(-1) }
            $slotLabel = $ScheduledSlot
        }
        $slotKey = $slotTime.ToString('yyyy-MM-ddTHH:mm')
        $slotStatePath = Join-Path $StateRoot "last-scheduled-slot-$Source.txt"
        if ((Test-Path $slotStatePath) -and (Read-CollectorUtf8 -Path $slotStatePath).Trim() -eq $slotKey) {
            Write-CollectorLog -Path $settings.log_file -Event "SKIPPED duplicate_slot=$slotKey"
            exit 11
        }
        $delayMinutes = [math]::Floor(($currentTime - $slotTime).TotalMinutes)
        $missedGrace = if ($ScheduledIntervalMinutes -gt 0) { 5 } else { [int]$settings.missed_run_grace_minutes }
        if ($delayMinutes -gt $missedGrace) {
            [IO.File]::WriteAllText($slotStatePath, $slotKey, [Text.Encoding]::ASCII)
            Write-CollectorLog -Path $settings.log_file -Event "MISSED slot=$slotKey delay_minutes=$delayMinutes recovery=skipped"
            Write-Host "Horário perdido há $delayMinutes min; recuperação fora da janela permitida."
            exit 10
        }
        if ($delayMinutes -gt 0) {
            Write-CollectorLog -Path $settings.log_file -Event "MISSED slot=$slotKey delay_minutes=$delayMinutes recovery=single_run"
        }
        [IO.File]::WriteAllText($slotStatePath, $slotKey, [Text.Encoding]::ASCII)
    }
    $runExitCode = 0
    foreach ($item in $sources) {
        if ($settings.sources.$item.mode -ne 'DIRECT' -or -not $settings.sources.$item.access_confirmed) {
            Write-CollectorLog -Path $settings.log_file -Event "SKIPPED source=$item mode_disabled_or_unconfirmed"
            Write-Host "$item ignorada: modo DIRECT e confirmação de acesso não configurados."
            if ($runExitCode -eq 0) { $runExitCode = 3 }
            continue
        }
        Write-CollectorLog -Path $settings.log_file -Event "START source=$item slot=$slotLabel"
        $backendDirectory = Join-Path $settings.repository_root 'backend'
        $outputPrefix = Join-Path (Join-Path $stateDir 'logs') ('.collector-' + [guid]::NewGuid().ToString('N'))
        $stdoutPath = "$outputPrefix.stdout"
        $stderrPath = "$outputPrefix.stderr"
        New-Item -ItemType Directory -Force -Path (Split-Path -Parent $outputPrefix) | Out-Null
        $env:PYTHONUTF8 = '1'
        $env:PYTHONIOENCODING = 'utf-8'
        try {
            $process = Start-Process -FilePath $python `
                -ArgumentList "-m app.scrapers.scraper_collector $item" `
                -WorkingDirectory $backendDirectory -Wait -PassThru -NoNewWindow `
                -RedirectStandardOutput $stdoutPath -RedirectStandardError $stderrPath
            foreach ($outputPath in @($stdoutPath, $stderrPath)) {
                if (Test-Path -LiteralPath $outputPath) {
                    $output = Read-CollectorUtf8 -Path $outputPath
                    foreach ($line in ($output -split "`r?`n")) {
                        if (-not [string]::IsNullOrEmpty($line)) {
                            Add-CollectorUtf8Line -Path $settings.log_file -Text $line
                        }
                    }
                }
            }
            $pythonExitCode = $process.ExitCode
        } finally {
            Remove-Item -LiteralPath $stdoutPath, $stderrPath -Force -ErrorAction SilentlyContinue
        }
        Write-CollectorLog -Path $settings.log_file -Event "FINISH source=$item exit=$pythonExitCode"
        if ($pythonExitCode -ne 0 -and $runExitCode -eq 0) { $runExitCode = $pythonExitCode }
        if (Test-Path $settings.stop_file) { break }
    }
    exit $runExitCode
} finally {
    $env:PYTHONUTF8 = $previousPythonUtf8
    $env:PYTHONIOENCODING = $previousPythonIoEncoding
    $env:SCRAPER_INGEST_HMAC_SECRET = $null
    $env:ARAUJO_STATE_DIR = $null
    $env:PACHECO_STATE_DIR = $null
    $env:SCRAPER_COLLECTOR_STATE_DIR = $null
    $env:SCRAPER_EXTERNAL_RATE_LIMIT_ENABLED = $null
    $env:SCRAPER_EXTERNAL_RATE_LIMIT_MAX_REQUESTS = $null
    $env:SCRAPER_EXTERNAL_RATE_LIMIT_WINDOW_SECONDS = $null
    $env:SCRAPER_EXTERNAL_RATE_LIMIT_STATE_DIR = $null
}
