param(
    [switch]$Enable,
    [Parameter(Mandatory = $true)]
    [ValidateSet('araujo', 'pacheco')]
    [string]$Source,
    [switch]$BackupVerified
)

$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'Collector-IO.ps1')
. (Join-Path $PSScriptRoot 'Collector-Schedule.ps1')
if (-not $Enable) { throw 'O agendamento fica desligado. Passe -Enable apenas após aprovar o piloto manual.' }
if (-not $BackupVerified) { throw 'Valide o backup atual antes de registrar a recuperação periódica.' }
if ([TimeZoneInfo]::Local.Id -notin @('E. South America Standard Time', 'SA Eastern Standard Time')) {
    throw 'Configure o fuso do Windows para America/Sao_Paulo (UTC-03:00).'
}
$StateRoot = Join-Path $env:LOCALAPPDATA 'PrecoBaoCollector'
$settingsPath = Join-Path $StateRoot 'settings.json'
if (-not (Test-Path -LiteralPath $settingsPath)) { throw 'Coletor ainda não instalado.' }
$settings = Read-CollectorJson -Path $settingsPath
if (-not $settings.api_ready -or $settings.sources.$Source.mode -ne 'DIRECT' -or -not $settings.sources.$Source.access_confirmed) {
    throw 'API e origem precisam estar configuradas e autorizadas no ambiente local.'
}
$runner = Join-Path $PSScriptRoot 'Invoke-ImageBackfill.ps1'
$root = [IO.Path]::GetFullPath([string]$settings.repository_root).TrimEnd('\', '/')
$expected = [IO.Path]::GetFullPath((Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path).TrimEnd('\', '/')
if (-not $root.Equals($expected, [StringComparison]::OrdinalIgnoreCase)) {
    throw 'A instalação do coletor aponta para outra cópia do repositório.'
}
$rateStatePath = Join-Path ([IO.Path]::GetFullPath([string]$settings.state_dir)) 'rate-limit\external-requests.json'
if (Test-Path -LiteralPath $rateStatePath) {
    $rateState = Read-CollectorJson -Path $rateStatePath
    if ($rateState.version -ne 1 -or $rateState.limit -ne 50 -or $rateState.window_seconds -ne 900) {
        throw 'O limite compartilhado salvo não é 50 requisições em 900 segundos.'
    }
}
$progress = Join-Path $StateRoot "state\image-backfill\progress-$Source.json"
if (-not (Test-Path -LiteralPath $progress)) { throw 'Execute e valide primeiro um piloto manual desta origem.' }
$pilot = Read-CollectorJson -Path $progress
if ([int]$pilot.last_run.stored -lt 1) { throw 'O piloto não confirmou nenhuma imagem armazenada.' }
$outbox = Join-Path ([IO.Path]::GetFullPath([string]$settings.state_dir)) "outbox\images\image-backfill\$Source"
if ((Test-Path -LiteralPath $outbox) -and @(Get-ChildItem -LiteralPath $outbox -File -Filter '*.json').Count -gt 0) {
    throw 'Há uploads pendentes; resolva-os antes de habilitar a tarefa.'
}

$taskName = "PrecoBaoImageBackfill-$Source-15m"
if (Get-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue) {
    throw "A tarefa $taskName já existe; nenhuma tarefa foi alterada."
}
$offset = if ($Source -eq 'araujo') { 0 } else { 7 }
$nextSlot = Get-NextCollectorSlot -Now (Get-Date) -OffsetMinutes $offset
$trigger = New-CollectorRepeatingTrigger -OffsetMinutes $offset -StartAt $nextSlot
$action = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$runner`" -Mode Execute -Source $Source -Limit 10 -BackupVerified"
$settingsTask = New-ScheduledTaskSettingsSet -StartWhenAvailable -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Minutes 12)
$principal = New-ScheduledTaskPrincipal -UserId ([Security.Principal.WindowsIdentity]::GetCurrent().Name) -LogonType Interactive
if ($trigger.Repetition.Interval -ne 'PT15M' -or $trigger.Repetition.Duration -ne 'P1D') { throw 'Trigger de 15 minutos inválido.' }
Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Settings $settingsTask -Principal $principal -ErrorAction Stop | Out-Null
$registered = Get-ScheduledTask -TaskName $taskName -ErrorAction Stop
if ($registered.State -ne 'Ready' -or $registered.Settings.MultipleInstances -ne 'IgnoreNew') {
    Disable-ScheduledTask -TaskName $taskName -ErrorAction SilentlyContinue | Out-Null
    throw 'A tarefa registrada não corresponde à configuração validada.'
}
Write-Host "Agendamento de imagens habilitado para $Source a cada 15 minutos, com offset $offset, teto 10 chamadas de backfill/origem e orçamento compartilhado 50/15 min."
