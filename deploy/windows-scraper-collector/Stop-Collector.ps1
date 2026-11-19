$ErrorActionPreference = 'Stop'
$StateRoot = Join-Path $env:LOCALAPPDATA 'PrecoBaoCollector'
$settings = Get-Content -Raw (Join-Path $StateRoot 'settings.json') | ConvertFrom-Json
Set-Content -Path $settings.stop_file -Value "stop requested $(Get-Date -Format o)" -Encoding utf8
Get-ScheduledTask -TaskName 'PrecoBaoCollector-*' -ErrorAction SilentlyContinue |
    Disable-ScheduledTask | Out-Null
Add-Content $settings.log_file "$(Get-Date -Format o) STOP_REQUESTED after_current_page"
Write-Host 'Novas coletas suspensas. A execução ativa termina após sincronizar a página/lote atual.'
