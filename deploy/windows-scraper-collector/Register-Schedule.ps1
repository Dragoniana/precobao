param([switch]$Enable)
$ErrorActionPreference = 'Stop'
$StateRoot = Join-Path $env:LOCALAPPDATA 'PrecoBaoCollector'
$settings = Get-Content -Raw (Join-Path $StateRoot 'settings.json') | ConvertFrom-Json
if (-not $Enable) { throw 'Agendamento inativo por padrão. Passe -Enable somente após API e testes finais.' }
if (-not $settings.api_ready -or -not $settings.collector_enabled) {
    throw 'Marque api_ready e collector_enabled após deploy aprovado e testes finais.'
}
if ([TimeZoneInfo]::Local.Id -notin @('E. South America Standard Time', 'SA Eastern Standard Time')) {
    throw 'Configure o fuso do Windows para America/Sao_Paulo (UTC-03:00).'
}
$script = Join-Path $PSScriptRoot 'Invoke-Collector.ps1'
foreach ($slot in $settings.schedule_times) {
    $parts = $slot.Split(':')
    $at = [DateTime]::Today.AddHours([int]$parts[0]).AddMinutes([int]$parts[1])
    $name = "PrecoBaoCollector-$($parts[0])$($parts[1])"
    $action = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$script`" -Source both -ScheduledSlot `"$slot`""
    $trigger = New-ScheduledTaskTrigger -Daily -At $at
    $settingsTask = New-ScheduledTaskSettingsSet -StartWhenAvailable -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Hours 4)
    $principal = New-ScheduledTaskPrincipal -UserId ([Security.Principal.WindowsIdentity]::GetCurrent().Name) -LogonType Interactive
    Register-ScheduledTask -TaskName $name -Action $action -Trigger $trigger -Settings $settingsTask -Principal $principal -Force | Out-Null
}
Write-Host 'Tarefas 08:00/20:00 instaladas. Para remover, execute Unregister-Schedule.ps1.'
