param(
    [switch]$Enable,
    [Parameter(Mandatory = $true)]
    [ValidateSet('araujo', 'pacheco')]
    [string]$Source
)

$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'Collector-IO.ps1')
. (Join-Path $PSScriptRoot 'Collector-Schedule.ps1')
$StateRoot = Join-Path $env:LOCALAPPDATA 'PrecoBaoCollector'
$settingsPath = Join-Path $StateRoot 'settings.json'
if (-not (Test-Path -LiteralPath $settingsPath)) { throw 'Coletor ainda não instalado.' }
$settings = Read-CollectorJson -Path $settingsPath
if (-not $Enable) { throw 'Passe -Enable somente após validar código, API e orçamento persistente.' }
if (-not $settings.api_ready -or -not $settings.collector_enabled) {
    throw 'A configuração local da API e do coletor precisa estar habilitada.'
}
if ([TimeZoneInfo]::Local.Id -notin @('E. South America Standard Time', 'SA Eastern Standard Time')) {
    throw 'Configure o fuso do Windows para America/Sao_Paulo (UTC-03:00).'
}
$repositoryRoot = [IO.Path]::GetFullPath([string]$settings.repository_root).TrimEnd('\', '/')
$requestedRoot = [IO.Path]::GetFullPath((Resolve-Path (Join-Path $PSScriptRoot '..\..')).Path).TrimEnd('\', '/')
if (-not $repositoryRoot.Equals($requestedRoot, [StringComparison]::OrdinalIgnoreCase)) {
    throw 'O coletor instalado aponta para outra cópia do repositório; nenhuma tarefa foi alterada.'
}
$sourceSettings = $settings.sources.$Source
if ($sourceSettings.mode -ne 'DIRECT' -or -not $sourceSettings.access_confirmed) {
    throw "$Source precisa estar em DIRECT com confirmação de acesso antes de agendar."
}
$secretPath = Join-Path $StateRoot 'hmac-secret.dpapi'
if (Test-Path -LiteralPath $secretPath -PathType Leaf) {
    $encryptedSecret = Read-CollectorUtf8 -Path $secretPath
    $secureSecret = ConvertTo-SecureString -String $encryptedSecret -ErrorAction Stop
    try {
        if ($secureSecret.Length -lt 32) { throw 'O segredo HMAC DPAPI descriptografado é menor que 32 caracteres.' }
    } finally { $secureSecret.Dispose() }
} else {
    throw 'O segredo HMAC DPAPI não está configurado.'
}

$stateDir = [IO.Path]::GetFullPath([string]$settings.state_dir)
$rateStatePath = Join-Path $stateDir 'rate-limit\external-requests.json'
if (Test-Path -LiteralPath $rateStatePath) {
    $rateState = Read-CollectorJson -Path $rateStatePath
    if ($rateState.version -ne 1 -or $rateState.limit -ne 50 -or $rateState.window_seconds -ne 900) {
        throw 'O orçamento salvo não corresponde a 50 requisições em 900 segundos; agendamento interrompido.'
    }
}

$newTaskName = "PrecoBaoCollector-$Source-15m"
$existingNewTask = Get-ScheduledTask -TaskName $newTaskName -ErrorAction SilentlyContinue
$offset = if ($Source -eq 'araujo') { 0 } else { 7 }
$nextSlot = Get-NextCollectorSlot -Now (Get-Date) -OffsetMinutes $offset
$trigger = New-CollectorRepeatingTrigger -OffsetMinutes $offset -StartAt $nextSlot
$script = Join-Path $PSScriptRoot 'Invoke-Collector.ps1'
$action = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$script`" -Source $Source -ScheduledIntervalMinutes 15 -ScheduledOffsetMinutes $offset"
$taskSettings = New-ScheduledTaskSettingsSet -StartWhenAvailable -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Hours 2)
$principal = New-ScheduledTaskPrincipal -UserId ([Security.Principal.WindowsIdentity]::GetCurrent().Name) -LogonType Interactive
if ($trigger.Repetition.Interval -ne 'PT15M' -or $trigger.Repetition.Duration -ne 'P1D') {
    throw 'O trigger recorrente falhou na validação; nenhuma tarefa foi alterada.'
}
if ($existingNewTask) {
    $actions = @($existingNewTask.Actions)
    if (
        $actions.Count -ne 1 -or
        $actions[0].Arguments -notmatch "-Source $Source .* -ScheduledIntervalMinutes 15 -ScheduledOffsetMinutes $offset" -or
        $existingNewTask.Principal.LogonType -ne 'Interactive'
    ) {
        throw "A tarefa $newTaskName já existe com configuração diferente; não foi sobrescrita."
    }
    if ($existingNewTask.State -eq 'Ready') {
        Write-Host "Tarefa já ativa e preservada: $newTaskName"
        return
    }
    throw "A tarefa $newTaskName existe, mas não está pronta. Revise o estado antes de reativar; nenhuma tarefa foi alterada."
}

$legacyNames = @(
    'PrecoBaoCollector-araujo-0800',
    'PrecoBaoCollector-araujo-2000',
    'PrecoBaoCollector-pacheco-1000',
    'PrecoBaoCollector-pacheco-2200'
)
$legacyTasks = @(Get-ScheduledTask -ErrorAction SilentlyContinue | Where-Object { $_.TaskName -in $legacyNames })

if ($Source -eq 'araujo' -and $legacyTasks.Count -gt 0) {
    if ($legacyTasks.Count -ne 4) {
        throw 'As quatro tarefas antigas esperadas não foram encontradas; parei sem alterar o Agendador.'
    }
    $backupRoot = Join-Path $StateRoot (Join-Path 'backups' ('scheduler-' + (Get-Date -Format 'yyyyMMdd-HHmmss')))
    [void][IO.Directory]::CreateDirectory($backupRoot)
    $backupManifest = [System.Collections.Generic.List[object]]::new()
    foreach ($task in $legacyTasks) {
        $xml = Export-ScheduledTask -TaskName $task.TaskName -ErrorAction Stop
        Write-CollectorUtf8 -Path (Join-Path $backupRoot ($task.TaskName + '.xml')) -Text $xml
        $backupManifest.Add([pscustomobject]@{
            name = $task.TaskName
            state = [string]$task.State
            path = ($task.TaskName + '.xml')
        })
    }
    Write-CollectorJson -Path (Join-Path $backupRoot 'manifest.json') -Value @{
        created_at = (Get-Date -Format o)
        branch = 'codex/scraper-integration-20261120'
        previous_tasks = @($backupManifest)
    }
    foreach ($task in $legacyTasks) {
        Disable-ScheduledTask -TaskName $task.TaskName -ErrorAction Stop | Out-Null
    }
    Write-Host "As quatro tarefas antigas foram exportadas e suspensas. Backup: $backupRoot"
}

if ($Source -eq 'pacheco') {
    $araujoTask = Get-ScheduledTask -TaskName 'PrecoBaoCollector-araujo-15m' -ErrorAction SilentlyContinue
    if (-not $araujoTask -or $araujoTask.State -ne 'Ready') {
        throw 'A etapa Pacheco só pode começar depois de a tarefa Araújo 15m estar pronta.'
    }
    $araujoInfo = Get-ScheduledTaskInfo -TaskName 'PrecoBaoCollector-araujo-15m' -ErrorAction Stop
    $araujoStatePath = Join-Path $stateDir 'araujo.json'
    if (-not (Test-Path -LiteralPath $araujoStatePath)) {
        throw 'Ainda não existe estado de coleta da Araújo.'
    }
    $araujoState = Read-CollectorJson -Path $araujoStatePath
    $allowedTaskResults = @(0, 3)
    if (
        $araujoInfo.LastRunTime -lt [DateTime]::new(2000, 1, 1) -or
        $araujoInfo.LastTaskResult -notin $allowedTaskResults -or
        $araujoState.execution_trigger -ne 'scheduled' -or
        [int]$araujoState.products_sent -lt 1 -or
        [int]$araujoState.batches_sent -lt 1 -or
        -not $araujoState.last_batch_id -or
        (Test-Path -LiteralPath (Join-Path $stateDir 'outbox\araujo.pending.json'))
    ) {
        throw 'Araújo ainda não concluiu uma execução agendada com lote confirmado na API.'
    }
}

try {
    Register-ScheduledTask -TaskName $newTaskName -Action $action -Trigger $trigger -Settings $taskSettings -Principal $principal -ErrorAction Stop | Out-Null
    $registered = Get-ScheduledTask -TaskName $newTaskName -ErrorAction Stop
    $registeredTrigger = @($registered.Triggers)[0]
    $registeredStart = [DateTimeOffset]::Parse([string]$registeredTrigger.StartBoundary).LocalDateTime
    if (
        $registered.State -ne 'Ready' -or
        $registered.Principal.LogonType -ne 'Interactive' -or
        $registeredStart.ToString('HH:mm') -ne $nextSlot.ToString('HH:mm') -or
        $registeredTrigger.Repetition.Interval -ne 'PT15M' -or
        $registeredTrigger.Repetition.Duration -ne 'P1D' -or
        $registered.Settings.MultipleInstances -ne 'IgnoreNew'
    ) {
        throw 'A tarefa registrada não corresponde ao trigger validado.'
    }
} catch {
    Disable-ScheduledTask -TaskName $newTaskName -ErrorAction SilentlyContinue | Out-Null
    throw
}

Write-Host "Tarefa $newTaskName ativa a cada 15 minutos, a partir de $nextSlot; orçamento global 50/15 min; sem limite por quantidade de produtos."
