$ErrorActionPreference = 'Stop'
$names = @('PrecoBaoImageBackfill-araujo-15m', 'PrecoBaoImageBackfill-pacheco-15m')
foreach ($name in $names) {
    $task = Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue
    if ($task) {
        if ($task.State -eq 'Running') { Stop-ScheduledTask -TaskName $name -ErrorAction Stop }
        Unregister-ScheduledTask -TaskName $name -Confirm:$false -ErrorAction Stop
        Write-Host "Removida somente a tarefa de imagens $name."
    }
}
