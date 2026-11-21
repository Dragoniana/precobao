$StateRoot = Join-Path $env:LOCALAPPDATA 'PrecoBaoCollector'
$settingsPath = Join-Path $StateRoot 'settings.json'
if (-not (Test-Path $settingsPath)) { throw 'Coletor ainda não instalado.' }
$settings = Get-Content -Raw $settingsPath | ConvertFrom-Json
Write-Host "Ativo: $($settings.collector_enabled); API pronta: $($settings.api_ready); fuso: $($settings.timezone)"
foreach ($source in @('araujo', 'pacheco')) {
    $path = Join-Path $settings.state_dir "$source.json"
    if (Test-Path $path) {
        $state = Get-Content -Raw $path | ConvertFrom-Json
        Write-Host "$source`: $($state.status) aceitos=$($state.products_sent) rejeitados=$($state.products_rejected) atualizado=$($state.finished_at) motivo=$($state.stop_reason)"
    } else { Write-Host "$source`: sem execução" }
    $rejectedPath = Join-Path $settings.state_dir "rejected\$source.json"
    if (Test-Path $rejectedPath) {
        $rejected = @(Get-Content -Raw $rejectedPath | ConvertFrom-Json)
        Write-Host "$source`: conflitos persistidos=$($rejected.Count)"
    }
    $imageOutbox = Join-Path $settings.state_dir "outbox\images\$source"
    $imageCount = if (Test-Path $imageOutbox) {
        @(Get-ChildItem -LiteralPath $imageOutbox -File -Filter '*.json').Count
    } else { 0 }
    Write-Host "$source`: imagens pendentes=$imageCount"
}
Get-ScheduledTask -ErrorAction SilentlyContinue |
    Where-Object { $_.TaskName -like 'PrecoBaoCollector-*' } |
    Select-Object TaskName, State
Write-Host "Log: $($settings.log_file)"
