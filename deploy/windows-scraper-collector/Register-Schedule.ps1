param([switch]$Enable)
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'Collector-IO.ps1')
$StateRoot = Join-Path $env:LOCALAPPDATA 'PrecoBaoCollector'
$settingsPath = Join-Path $StateRoot 'settings.json'
if (-not (Test-Path -LiteralPath $settingsPath)) { throw 'Coletor ainda não instalado.' }
$settings = Read-CollectorJson -Path $settingsPath
if (-not $Enable) { throw 'Agendamento inativo por padrão. Passe -Enable somente após API e testes finais.' }
if (-not $settings.api_ready -or -not $settings.collector_enabled) {
    throw 'Marque api_ready e collector_enabled após deploy aprovado e testes finais.'
}
if ([TimeZoneInfo]::Local.Id -notin @('E. South America Standard Time', 'SA Eastern Standard Time')) {
    throw 'Configure o fuso do Windows para America/Sao_Paulo (UTC-03:00).'
}
$limit = 0
if (-not [int]::TryParse([string]$settings.max_products_per_run, [ref]$limit) -or $limit -lt 1 -or $limit -gt 50) {
    throw 'max_products_per_run deve estar configurado entre 1 e 50.'
}
if (-not $settings.source_schedule_times) {
    throw 'Configure horários separados para Araújo e Pacheco.'
}
$legacyTasks = @(
    Get-ScheduledTask -ErrorAction SilentlyContinue | Where-Object {
        $_.TaskName -in @('PrecoBaoCollector-0800', 'PrecoBaoCollector-2000')
    }
)
if ($legacyTasks.Count -gt 0) {
    throw 'Foram encontradas tarefas antigas compartilhadas. Remova-as com Unregister-Schedule.ps1 antes de registrar as tarefas separadas.'
}

$schedule = @{}
$allTimes = @{}
$sourcesToSchedule = @()
foreach ($candidate in @('araujo', 'pacheco')) {
    $sourceSettings = $settings.sources.$candidate
    if ($sourceSettings.mode -eq 'DIRECT' -and $sourceSettings.access_confirmed) {
        $sourcesToSchedule += $candidate
    }
}
if ($sourcesToSchedule.Count -eq 0) {
    throw 'Nenhuma fonte está habilitada e confirmada para coleta direta.'
}
foreach ($source in $sourcesToSchedule) {
    $times = @($settings.source_schedule_times.$source)
    if ($times.Count -ne 2) { throw "Configure dois horários para $source." }
    $schedule[$source] = @()
    foreach ($slot in $times) {
        if ([string]$slot -notmatch '^(?:[01]\d|2[0-3]):[0-5]\d$') {
            throw "Horário inválido em $source. Use HH:mm."
        }
        if ($allTimes.ContainsKey([string]$slot)) {
            throw "Horários simultâneos não são permitidos: $slot ($source e $($allTimes[[string]$slot]))."
        }
        $allTimes[[string]$slot] = $source
        $schedule[$source] += [string]$slot
    }
}

$script = Join-Path $PSScriptRoot 'Invoke-Collector.ps1'
$definitions = @()
foreach ($source in $sourcesToSchedule) {
    foreach ($slot in $schedule[$source]) {
        $parts = $slot.Split(':')
        $name = "PrecoBaoCollector-$source-$($parts[0])$($parts[1])"
        $action = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$script`" -Source $source -MaxProductsPerRun $limit -ScheduledSlot `"$slot`""
        $trigger = New-ScheduledTaskTrigger -Daily -At ([DateTime]::Today.AddHours([int]$parts[0]).AddMinutes([int]$parts[1]))
        $settingsTask = New-ScheduledTaskSettingsSet -StartWhenAvailable -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Hours 2)
        $principal = New-ScheduledTaskPrincipal -UserId ([Security.Principal.WindowsIdentity]::GetCurrent().Name) -LogonType Interactive
        $definitions += [pscustomobject]@{ Name = $name; Action = $action; Trigger = $trigger; Settings = $settingsTask; Principal = $principal }
    }
}

# Validate all definitions before touching Task Scheduler.
foreach ($definition in $definitions) {
    $existing = Get-ScheduledTask -TaskName $definition.Name -ErrorAction SilentlyContinue
    if ($existing) {
        $existingAction = @($existing.Actions)[0]
        $expectedTime = ([DateTime]$definition.Trigger.StartBoundary).ToString('HH:mm')
        $existingTimes = @($existing.Triggers | ForEach-Object {
            ([DateTime]$_.StartBoundary).ToString('HH:mm')
        })
        if (
            $existingAction.Execute -ne $definition.Action.Execute -or
            $existingAction.Arguments -ne $definition.Action.Arguments -or
            $existing.Principal.LogonType -ne 'Interactive' -or
            $expectedTime -notin $existingTimes
        ) {
            throw "A tarefa existente $($definition.Name) difere da configuração aprovada; nenhuma tarefa foi sobrescrita."
        }
    }
}
foreach ($definition in $definitions) {
    if (Get-ScheduledTask -TaskName $definition.Name -ErrorAction SilentlyContinue) {
        Write-Host "Tarefa existente preservada: $($definition.Name)"
        continue
    }
    Register-ScheduledTask -TaskName $definition.Name -Action $definition.Action -Trigger $definition.Trigger -Settings $definition.Settings -Principal $definition.Principal | Out-Null
}
Write-Host "Tarefas separadas instaladas; limite de $limit produtos por farmácia e execução. Araújo: $($schedule.araujo -join ', '); Pacheco: $($schedule.pacheco -join ', ')."
