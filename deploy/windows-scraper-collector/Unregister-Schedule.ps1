Get-ScheduledTask -TaskName 'PrecoBaoCollector-*' -ErrorAction SilentlyContinue |
    Unregister-ScheduledTask -Confirm:$false
Write-Host 'Agendamento removido.'
