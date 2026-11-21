Get-ScheduledTask -ErrorAction SilentlyContinue |
    Where-Object { $_.TaskName -like 'PrecoBaoCollector-*' } |
    Unregister-ScheduledTask -Confirm:$false
Write-Host 'Agendamento removido.'
