$StateRoot = Join-Path $env:LOCALAPPDATA 'PrecoBaoCollector'
$settingsPath = Join-Path $StateRoot 'settings.json'
if (-not (Test-Path $settingsPath)) { throw 'Coletor ainda não instalado.' }
$settings = Get-Content -Raw $settingsPath | ConvertFrom-Json
Write-Host "Ativo: $($settings.collector_enabled); API pronta: $($settings.api_ready); fuso: $($settings.timezone)"
foreach ($source in @('araujo', 'pacheco')) {
    $path = Join-Path $settings.state_dir "$source.json"
    if (Test-Path $path) {
        $state = Get-Content -Raw $path | ConvertFrom-Json
        Write-Host "$source`: $($state.status) produtos=$($state.products_sent) atualizado=$($state.finished_at) motivo=$($state.stop_reason)"
    } else { Write-Host "$source`: sem execução" }
}
Get-ScheduledTask -TaskName 'PrecoBaoCollector-*' -ErrorAction SilentlyContinue |
    Select-Object TaskName, State
Write-Host "Log: $($settings.log_file)"
