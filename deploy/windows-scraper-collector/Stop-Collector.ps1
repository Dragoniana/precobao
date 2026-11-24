$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'Collector-IO.ps1')
$StateRoot = Join-Path $env:LOCALAPPDATA 'PrecoBaoCollector'
$settings = Read-CollectorJson -Path (Join-Path $StateRoot 'settings.json')
Write-CollectorUtf8 -Path $settings.stop_file -Text "stop requested $(Get-Date -Format o)"
Get-ScheduledTask -TaskName 'PrecoBaoCollector-*' -ErrorAction SilentlyContinue |
    Disable-ScheduledTask | Out-Null
Write-CollectorLog -Path $settings.log_file -Event 'STOP_REQUESTED after_current_page'
Write-Host 'Novas coletas suspensas. A execução ativa termina após sincronizar a página/lote atual.'
